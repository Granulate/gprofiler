#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import concurrent.futures
import datetime
import logging
import logging.config
import logging.handlers
import os
import signal
import sys
import time
from logging import Logger
from pathlib import Path
from threading import Event
from typing import Callable, Dict, Optional

import configargparse
from requests import RequestException, Timeout

from gprofiler import __version__, merge
from gprofiler.client import DEFAULT_UPLOAD_TIMEOUT, GRANULATE_SERVER_HOST, APIClient, APIError
from gprofiler.docker_client import DockerClient
from gprofiler.java import JavaProfiler
from gprofiler.merge import ProcessToStackSampleCounters
from gprofiler.perf import SystemProfiler
from gprofiler.php import PHPSpyProfiler
from gprofiler.profiler_base import NoopProfiler
from gprofiler.python import get_python_profiler
from gprofiler.ruby import RbSpyProfiler
from gprofiler.utils import (
    TEMPORARY_STORAGE_PATH,
    TemporaryDirectoryWithMode,
    atomically_symlink,
    get_hostname,
    get_iso8601_format_time,
    grab_gprofiler_mutex,
    is_root,
    is_running_in_init_pid,
    log_system_info,
    reset_umask,
    resource_path,
    run_process,
)

logger: Logger

DEFAULT_LOG_FILE = "/var/log/gprofiler/gprofiler.log"
DEFAULT_LOG_MAX_SIZE = 1024 * 1024 * 5
DEFAULT_LOG_BACKUP_COUNT = 1

DEFAULT_PROFILING_DURATION = datetime.timedelta(seconds=60).seconds
DEFAULT_SAMPLING_FREQUENCY = 11
# by default - these match
DEFAULT_CONTINUOUS_MODE_INTERVAL = DEFAULT_PROFILING_DURATION
# 1 KeyboardInterrupt raised per this many seconds, no matter how many SIGINTs we get.
SIGINT_RATELIMIT = 0.5

last_signal_ts: Optional[float] = None


def sigint_handler(sig, frame):
    global last_signal_ts
    ts = time.monotonic()
    # no need for atomicity here: we can't get another SIGINT before this one returns.
    # https://www.gnu.org/software/libc/manual/html_node/Signals-in-Handler.html#Signals-in-Handler
    if last_signal_ts is None or ts > last_signal_ts + SIGINT_RATELIMIT:
        last_signal_ts = ts
        raise KeyboardInterrupt


def create_profiler_or_noop(runtimes: Dict[str, bool], profiler_constructor_callback: Callable, runtime_name: str):
    # disabled?
    if not runtimes[runtime_name]:
        return NoopProfiler()

    try:
        return profiler_constructor_callback()
    except Exception:
        logger.exception(f"Couldn't create {runtime_name} profiler, continuing without this runtime profiler")
        return NoopProfiler()


class GProfiler:
    def __init__(
        self,
        frequency: int,
        duration: int,
        output_dir: str,
        flamegraph: bool,
        rotating_output: bool,
        perf_mode: str,
        dwarf_stack_size: int,
        runtimes: Dict[str, bool],
        client: APIClient,
        include_container_names=True,
        php_process_filter: str = PHPSpyProfiler.DEFAULT_PROCESS_FILTER,
    ):
        self._frequency = frequency
        self._duration = duration
        self._output_dir = output_dir
        self._flamegraph = flamegraph
        self._runtimes = runtimes
        self._rotating_output = rotating_output
        self._client = client
        self._stop_event = Event()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        # TODO: we actually need 2 types of temporary directories.
        # 1. accessible by everyone - for profilers that run code in target processes, like async-profiler
        # 2. accessible only by us.
        # the latter can be root only. the former can not. we should do this separation so we don't expose
        # files unnecessarily.
        self._temp_storage_dir = TemporaryDirectoryWithMode(dir=TEMPORARY_STORAGE_PATH, mode=0o755)
        self.java_profiler = create_profiler_or_noop(
            self._runtimes,
            lambda: JavaProfiler(self._frequency, self._duration, self._stop_event, self._temp_storage_dir.name),
            "java",
        )
        self.system_profiler = create_profiler_or_noop(
            self._runtimes,
            lambda: SystemProfiler(
                self._frequency,
                self._duration,
                self._stop_event,
                self._temp_storage_dir.name,
                perf_mode,
                dwarf_stack_size,
            ),
            "system",
        )
        self._initialize_python_profiler()
        self.php_profiler = create_profiler_or_noop(
            self._runtimes,
            lambda: PHPSpyProfiler(
                self._frequency, self._duration, self._stop_event, self._temp_storage_dir.name, php_process_filter
            ),
            "php",
        )
        self.ruby_profiler = create_profiler_or_noop(
            self._runtimes,
            lambda: RbSpyProfiler(self._frequency, self._duration, self._stop_event, self._temp_storage_dir.name),
            "ruby",
        )
        self._docker_client = DockerClient()
        self._include_container_names = include_container_names

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def _initialize_python_profiler(self) -> None:
        self.python_profiler = create_profiler_or_noop(
            self._runtimes,
            lambda: get_python_profiler(
                self._frequency,
                self._duration,
                self._stop_event,
                self._temp_storage_dir.name,
                self._initialize_python_profiler,
            ),
            "python",
        )

    def _update_last_output(self, last_output_name: str, output_path: str) -> None:
        last_output = os.path.join(self._output_dir, last_output_name)
        prev_output = Path(last_output).resolve()
        atomically_symlink(os.path.basename(output_path), last_output)
        # delete if rotating & there was a link target before.
        if self._rotating_output and os.path.basename(prev_output) != last_output_name:
            # can't use missing_ok=True, available only from 3.8 :/
            try:
                prev_output.unlink()
            except FileNotFoundError:
                pass

    def _generate_output_files(
        self,
        collapsed_data: str,
        local_start_time: datetime.datetime,
        local_end_time: datetime.datetime,
    ) -> None:
        start_ts = get_iso8601_format_time(local_start_time)
        end_ts = get_iso8601_format_time(local_end_time)
        base_filename = os.path.join(self._output_dir, "profile_{}".format(end_ts))

        collapsed_path = base_filename + ".col"
        collapsed_data = self._strip_container_data(collapsed_data)
        Path(collapsed_path).write_text(collapsed_data)

        # point last_profile.col at the new file; and possibly, delete the previous one.
        self._update_last_output("last_profile.col", collapsed_path)
        logger.info(f"Saved collapsed stacks to {collapsed_path}")

        if self._flamegraph:
            flamegraph_path = base_filename + ".html"
            flamegraph_data = (
                Path(resource_path("flamegraph/flamegraph_template.html"))
                .read_text()
                .replace(
                    "{{{JSON_DATA}}}",
                    run_process(
                        [resource_path("burn"), "convert", "--type=folded", collapsed_path], suppress_log=True
                    ).stdout.decode(),
                )
                .replace("{{{START_TIME}}}", start_ts)
                .replace("{{{END_TIME}}}", end_ts)
            )
            Path(flamegraph_path).write_text(flamegraph_data)

            # point last_flamegraph.html at the new file; and possibly, delete the previous one.
            self._update_last_output("last_flamegraph.html", flamegraph_path)

            logger.info(f"Saved flamegraph to {flamegraph_path}")

    @staticmethod
    def _strip_container_data(collapsed_data):
        lines = []
        for line in collapsed_data.splitlines():
            if line.startswith("#"):
                continue
            lines.append(line[line.find(';') + 1 :])
        return '\n'.join(lines)

    def start(self):
        self._stop_event.clear()

        for prof in (
            self.python_profiler,
            self.java_profiler,
            self.system_profiler,
            self.php_profiler,
            self.ruby_profiler,
        ):
            prof.start()

    def stop(self):
        logger.info("Stopping ...")
        self._stop_event.set()

        for prof in (
            self.python_profiler,
            self.java_profiler,
            self.system_profiler,
            self.php_profiler,
            self.ruby_profiler,
        ):
            prof.stop()

    def _snapshot(self):
        local_start_time = datetime.datetime.utcnow()
        monotonic_start_time = time.monotonic()

        java_future = self._executor.submit(self.java_profiler.snapshot)
        java_future.name = "java"
        python_future = self._executor.submit(self.python_profiler.snapshot)
        python_future.name = "python"
        php_future = self._executor.submit(self.php_profiler.snapshot)
        php_future.name = "php"
        ruby_future = self._executor.submit(self.ruby_profiler.snapshot)
        ruby_future.name = "ruby"
        system_future = self._executor.submit(self.system_profiler.snapshot)
        system_future.name = "system"

        process_perfs: ProcessToStackSampleCounters = {}
        for future in concurrent.futures.as_completed([java_future, python_future, php_future, ruby_future]):
            # if either of these fail - log it, and continue.
            try:
                process_perfs.update(future.result())
            except Exception:
                logger.exception(f"{future.name} profiling failed")

        local_end_time = local_start_time + datetime.timedelta(seconds=(time.monotonic() - monotonic_start_time))

        system_result = system_future.result()
        if self._runtimes["system"]:
            merged_result, total_samples = merge.merge_perfs(
                system_result,
                process_perfs,
                self._docker_client,
                self._include_container_names,
            )
        else:
            assert system_result == {}, system_result  # should be empty!
            merged_result, total_samples = merge.concatenate_perfs(
                process_perfs, self._docker_client, self._include_container_names
            )

        if self._output_dir:
            self._generate_output_files(merged_result, local_start_time, local_end_time)

        if self._client:
            try:
                self._client.submit_profile(local_start_time, local_end_time, merged_result, total_samples)
            except Timeout:
                logger.error("Upload of profile to server timed out.")
            except APIError as e:
                logger.error(f"Error occurred sending profile to server: {e}")
            except RequestException:
                logger.exception("Error occurred sending profile to server")
            else:
                logger.info("Successfully uploaded profiling data to the server")

    def run_single(self):
        with self:
            self._snapshot()

    def run_continuous(self, interval):
        with self:
            while not self._stop_event.is_set():
                start_time = time.monotonic()
                try:
                    self._snapshot()
                except Exception:
                    logger.exception("Profiling run failed!")
                time_spent = time.monotonic() - start_time
                self._stop_event.wait(max(interval - time_spent, 0))


def setup_logger(stream_level: int, log_file_path: str, rotate_max_bytes: int, rotate_backup_count: int):
    global logger
    logger = logging.getLogger("gprofiler")
    logger.setLevel(logging.DEBUG)

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setLevel(stream_level)
    if stream_level < logging.INFO:
        stream_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(name)s: %(message)s"))
    else:
        stream_handler.setFormatter(logging.Formatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
    logger.addHandler(stream_handler)

    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file_path,
        maxBytes=rotate_max_bytes,
        backupCount=rotate_backup_count,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s: %(name)s: %(message)s"))
    logger.addHandler(file_handler)


def parse_cmd_args():
    parser = configargparse.ArgumentParser(
        description="gprofiler",
        auto_env_var_prefix="gprofiler_",
        add_config_file_help=True,
        add_env_var_help=False,
        default_config_files=["/etc/gprofiler/config.ini"],
    )
    parser.add_argument("--config", is_config_file=True, help="Config file path")
    parser.add_argument(
        "-f",
        "--profiling-frequency",
        type=int,
        dest="frequency",
        default=DEFAULT_SAMPLING_FREQUENCY,
        help="Profiler frequency in Hz (default: %(default)s)",
    )
    parser.add_argument(
        "-d",
        "--profiling-duration",
        type=int,
        dest="duration",
        default=DEFAULT_PROFILING_DURATION,
        help="Profiler duration per session in seconds (default: %(default)s)",
    )
    parser.add_argument("-o", "--output-dir", type=str, help="Path to output directory")
    parser.add_argument(
        "--flamegraph", dest="flamegraph", action="store_true", help="Generate local flamegraphs when -o is given"
    )
    parser.add_argument(
        "--no-flamegraph",
        dest="flamegraph",
        action="store_false",
        help="Do not generate local flamegraphs when -o is given (only collapsed stacks files)",
    )
    parser.set_defaults(flamegraph=True)

    parser.add_argument(
        "--rotating-output", action="store_true", default=False, help="Keep only the last profile result"
    )

    parser.add_argument(
        "--no-java",
        dest="java",
        action="store_false",
        default=True,
        help="Do not invoke runtime-specific profilers for Java processes",
    )

    parser.add_argument(
        "--no-python",
        dest="python",
        action="store_false",
        default=True,
        help="Do not invoke runtime-specific profilers for Python processes",
    )

    parser.add_argument(
        "--no-php",
        dest="php",
        action="store_false",
        default=True,
        help="Do not invoke runtime-specific profilers for PHP processes",
    )
    parser.add_argument(
        "--php-proc-filter",
        dest="php_process_filter",
        default=PHPSpyProfiler.DEFAULT_PROCESS_FILTER,
        help="Process filter for php processes (default: %(default)s)",
    )
    parser.add_argument(
        "--no-ruby",
        dest="ruby",
        action="store_false",
        default=True,
        help="Do not invoke runtime-specific profilers for Ruby processes",
    )

    parser.add_argument(
        "--perf-mode",
        dest="perf_mode",
        default="fp",
        choices=["fp", "dwarf", "smart", "none"],
        help="Run perf with either FP (Frame Pointers), DWARF, or run both and intelligently merge them "
        "by choosing the best result per process. If 'none' is chosen, do not invoke 'perf' at all. The "
        "output, in that case, is the concatenation of the results from all of the runtime profilers.",
    )
    parser.add_argument(
        "--perf-dwarf-stack-size",
        dest="dwarf_stack_size",
        default=8192,
        type=int,
        help="The max stack size for the Dwarf perf, in bytes. Must be <=65528."
        " Relevant for --perf-mode dwarf|smart. Default: %(default)s",
    )

    parser.add_argument(
        "-u",
        "--upload-results",
        action="store_true",
        default=False,
        help="Whether to upload the profiling results to the server",
    )
    parser.add_argument("--server-host", default=GRANULATE_SERVER_HOST, help="Server host (default: %(default)s)")
    parser.add_argument(
        "--server-upload-timeout",
        type=int,
        default=DEFAULT_UPLOAD_TIMEOUT,
        help="Timeout for upload requests to the server in seconds (default: %(default)s)",
    )
    parser.add_argument("--token", dest="server_token", help="Server token")
    parser.add_argument("--service-name", help="Service name")

    parser.add_argument('--version', action='version', version=__version__)
    parser.add_argument("-v", "--verbose", action="store_true", default=False, dest="verbose")

    logging_options = parser.add_argument_group("logging")
    logging_options.add_argument("--log-file", action="store", type=str, dest="log_file", default=DEFAULT_LOG_FILE)
    logging_options.add_argument(
        "--log-rotate-max-size", action="store", type=int, dest="log_rotate_max_size", default=DEFAULT_LOG_MAX_SIZE
    )
    logging_options.add_argument(
        "--log-rotate-backup-count",
        action="store",
        type=int,
        dest="log_rotate_backup_count",
        default=DEFAULT_LOG_BACKUP_COUNT,
    )
    parser.add_argument(
        "--disable-container-names",
        action="store_true",
        dest="disable_container_names",
        default=False,
        help="gProfiler won't gather the container names of processes that run in containers",
    )

    continuous_command_parser = parser.add_argument_group("continuous")
    continuous_command_parser.add_argument(
        "--continuous", "-c", action="store_true", dest="continuous", help="Run in continuous mode"
    )
    continuous_command_parser.add_argument(
        "-i",
        "--profiling-interval",
        type=int,
        dest="continuous_profiling_interval",
        default=DEFAULT_CONTINUOUS_MODE_INTERVAL,
        help="Time between each profiling sessions in seconds (default: %(default)s). Note: this is the time between"
        " session starts, not between the end of one session to the beginning of the next one.",
    )

    args = parser.parse_args()

    if args.upload_results:
        if not args.server_token:
            parser.error("Must provide --token when --upload-results is passed")
        if not args.service_name:
            parser.error("Must provide --service-name when --upload-results is passed")

    if args.continuous and args.duration > args.continuous_profiling_interval:
        parser.error(
            "--profiling-duration must be lower or equal to --profiling-interval when profiling in continuous mode"
        )

    if not args.upload_results and not args.output_dir:
        parser.error("Must pass at least one output method (--upload-results / --output-dir)")

    if args.dwarf_stack_size > 65528:
        parser.error("--perf-dwarf-stack-size maximum size is 65528")

    if args.perf_mode in ("dwarf", "smart") and args.frequency > 100:
        parser.error("--profiling-frequency|-f can't be larger than 100 when using --perf-mode smart|dwarf")

    return args


def verify_preconditions():
    if not is_root():
        print("Must run gprofiler as root, please re-run.", file=sys.stderr)
        sys.exit(1)

    if not is_running_in_init_pid():
        print(
            "Please run me in the init PID namespace! In Docker, make sure you pass '--pid=host'."
            " In Kubernetes, add 'hostPID: true' in the Pod spec.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not grab_gprofiler_mutex():
        print("Could not acquire gProfiler's lock. Is it already running?", file=sys.stderr)
        sys.exit(1)


def setup_signals() -> None:
    # When we run under staticx & PyInstaller, both of them forward (some of the) signals to gProfiler.
    # We catch SIGINTs and ratelimit them, to avoid being interrupted again during the handling of the
    # first INT.
    # See my commit message for more information.
    signal.signal(signal.SIGINT, sigint_handler)
    # handle SIGTERM in the same manner - gracefully stop gProfiler.
    # SIGTERM is also forwarded by staticx & PyInstaller, so we need to ratelimit it.
    signal.signal(signal.SIGTERM, sigint_handler)


def main():
    args = parse_cmd_args()
    verify_preconditions()
    setup_logger(
        logging.DEBUG if args.verbose else logging.INFO,
        args.log_file,
        args.log_rotate_max_size,
        args.log_rotate_backup_count,
    )
    global logger  # silences flake8, who now knows that the "logger" global we refer to was initialized.

    setup_signals()
    reset_umask()

    try:
        logger.info(f"Running gprofiler (version {__version__}), commandline: {' '.join(sys.argv[1:])!r}")
        try:
            log_system_info()
        except Exception:
            logger.exception("Encountered an exception while getting basic system info")

        if args.output_dir:
            try:
                os.makedirs(args.output_dir, exist_ok=True)
            except (FileExistsError, NotADirectoryError):
                logger.error(
                    "Output directory / a component in its path already exists as a non-directory!"
                    f"Please check the path {args.output_dir!r}"
                )
                sys.exit(1)

        if not os.path.exists(TEMPORARY_STORAGE_PATH):
            os.mkdir(TEMPORARY_STORAGE_PATH)

        try:
            client_kwargs = {}
            if "server_upload_timeout" in args:
                client_kwargs["upload_timeout"] = args.server_upload_timeout
            client = (
                APIClient(args.server_host, args.server_token, args.service_name, get_hostname(), **client_kwargs)
                if args.upload_results
                else None
            )
        except APIError as e:
            logger.error(f"Server error: {e}")
            return
        except RequestException as e:
            logger.error(f"Failed to connect to server: {e}")
            return

        runtimes = {
            "system": args.perf_mode != "none",
            "java": args.java,
            "python": args.python,
            "php": args.php,
            "ruby": args.ruby,
        }
        gprofiler = GProfiler(
            args.frequency,
            args.duration,
            args.output_dir,
            args.flamegraph,
            args.rotating_output,
            args.perf_mode,
            args.dwarf_stack_size,
            runtimes,
            client,
            not args.disable_container_names,
            args.php_process_filter,
        )
        logger.info("gProfiler initialized and ready to start profiling")

        if args.continuous:
            gprofiler.run_continuous(args.continuous_profiling_interval)
        else:
            gprofiler.run_single()

    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("Unexpected error occurred")


if __name__ == "__main__":
    main()
