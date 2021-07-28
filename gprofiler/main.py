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
from pathlib import Path
from threading import Event
from typing import Callable, Dict, Optional, Union

import configargparse
from requests import RequestException, Timeout

from gprofiler import __version__, merge
from gprofiler.client import DEFAULT_UPLOAD_TIMEOUT, GRANULATE_SERVER_HOST, APIClient, APIError
from gprofiler.docker_client import DockerClient
from gprofiler.log import RemoteLogsHandler, initial_root_logger_setup
from gprofiler.merge import ProcessToStackSampleCounters
from gprofiler.metadata.metadata_collector import get_current_metadata, get_static_metadata
from gprofiler.metadata.metadata_type import Metadata
from gprofiler.metadata.system_metadata import get_hostname, get_run_mode_and_deployment_type
from gprofiler.profilers.java import JavaProfiler
from gprofiler.profilers.perf import SystemProfiler
from gprofiler.profilers.php import DEFAULT_PROCESS_FILTER, PHPSpyProfiler
from gprofiler.profilers.profiler_base import NoopProfiler
from gprofiler.profilers.python import PythonProfiler
from gprofiler.profilers.registry import get_profilers_registry
from gprofiler.profilers.ruby import RbSpyProfiler
from gprofiler.state import State, init_state
from gprofiler.system_metrics import NoopSystemMetricsMonitor, SystemMetricsMonitor, SystemMetricsMonitorBase
from gprofiler.types import positive_integer
from gprofiler.utils import (
    TEMPORARY_STORAGE_PATH,
    CpuUsageLogger,
    TemporaryDirectoryWithMode,
    atomically_symlink,
    get_iso8601_format_time,
    grab_gprofiler_mutex,
    is_root,
    is_running_in_init_pid,
    log_system_info,
    reset_umask,
    resource_path,
    run_process,
)

logger: logging.LoggerAdapter

DEFAULT_LOG_FILE = "/var/log/gprofiler/gprofiler.log"
DEFAULT_LOG_MAX_SIZE = 1024 * 1024 * 5
DEFAULT_LOG_BACKUP_COUNT = 1

DEFAULT_PROFILING_DURATION = datetime.timedelta(seconds=60).seconds
DEFAULT_SAMPLING_FREQUENCY = 11

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
        nodejs_mode: str,
        dwarf_stack_size: int,
        python_mode: str,
        pyperf_user_stacks_pages: Optional[int],
        runtimes: Dict[str, bool],
        client: APIClient,
        collect_metrics: bool,
        collect_metadata: bool,
        state: State,
        cpu_usage_logger: CpuUsageLogger,
        run_args: Dict[str, Union[bool, str, int]],
        include_container_names=True,
        profile_api_version: Optional[str] = None,
        remote_logs_handler: Optional[RemoteLogsHandler] = None,
        php_process_filter: str = DEFAULT_PROCESS_FILTER,
    ):
        self._frequency = frequency
        self._duration = duration
        self._output_dir = output_dir
        self._flamegraph = flamegraph
        self._runtimes = runtimes
        self._rotating_output = rotating_output
        self._client = client
        self._state = state
        self._remote_logs_handler = remote_logs_handler
        self._profile_api_version = profile_api_version
        self._collect_metrics = collect_metrics
        self._collect_metadata = collect_metadata
        self._stop_event = Event()
        self._static_metadata: Optional[Metadata] = None
        self._spawn_time = time.time()
        if collect_metadata and self._client is not None:
            self._static_metadata = get_static_metadata(spawn_time=self._spawn_time, run_args=run_args)
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
                nodejs_mode == "perf",
                dwarf_stack_size,
            ),
            "perf",
        )
        self.python_profiler = create_profiler_or_noop(
            self._runtimes,
            lambda: PythonProfiler(
                self._frequency,
                self._duration,
                self._stop_event,
                self._temp_storage_dir.name,
                python_mode,
                pyperf_user_stacks_pages,
            ),
            "python",
        )
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
        if include_container_names and profile_api_version != "v1":
            self._docker_client: Optional[DockerClient] = DockerClient()
        else:
            self._docker_client = None
        self._cpu_usage_logger = cpu_usage_logger
        if collect_metrics:
            self._system_metrics_monitor: SystemMetricsMonitorBase = SystemMetricsMonitor(self._stop_event)
        else:
            self._system_metrics_monitor = NoopSystemMetricsMonitor()

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

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
        self._system_metrics_monitor.start()

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
        self._system_metrics_monitor.stop()

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

        process_profiles: ProcessToStackSampleCounters = {}
        for future in concurrent.futures.as_completed([java_future, python_future, php_future, ruby_future]):
            # if either of these fail - log it, and continue.
            try:
                process_profiles.update(future.result())
            except Exception:
                logger.exception(f"{future.name} profiling failed")

        local_end_time = local_start_time + datetime.timedelta(seconds=(time.monotonic() - monotonic_start_time))

        try:
            system_result = system_future.result()
        except Exception:
            logger.exception(
                "Running perf failed; consider running gProfiler with '--perf-mode disabled' to avoid using perf"
            )
            raise
        metadata = (
            get_current_metadata(self._static_metadata)
            if self._collect_metadata and self._client
            else {"hostname": get_hostname()}
        )
        if self._runtimes["perf"]:
            merged_result, total_samples = merge.merge_profiles(
                system_result,
                process_profiles,
                self._docker_client,
                self._profile_api_version != "v1",
                metadata,
            )
        else:
            assert system_result == {}, system_result  # should be empty!
            merged_result, total_samples = merge.concatenate_profiles(
                process_profiles,
                self._docker_client,
                self._profile_api_version != "v1",
                metadata,
            )

        if self._output_dir:
            self._generate_output_files(merged_result, local_start_time, local_end_time)

        if self._client:
            metrics = self._system_metrics_monitor.get_metrics()
            try:
                self._client.submit_profile(
                    local_start_time,
                    local_end_time,
                    merged_result,
                    total_samples,
                    self._profile_api_version,
                    self._spawn_time,
                    metrics,
                )
            except Timeout:
                logger.error("Upload of profile to server timed out.")
            except APIError as e:
                logger.error(f"Error occurred sending profile to server: {e}")
            except RequestException:
                logger.exception("Error occurred sending profile to server")
            else:
                logger.info("Successfully uploaded profiling data to the server")

    def _send_remote_logs(self):
        """
        The function is safe to call without wrapping with try/except block, the function should does the exception
        handling by itself.
        """
        if self._remote_logs_handler is None:
            return

        try:
            self._remote_logs_handler.try_send_log_to_server()
        except Exception:
            logger.exception("Couldn't send logs to server")

    def run_single(self):
        with self:
            # In case of single run mode, use the same id for run_id and cycle_id
            self._state.set_cycle_id(self._state.run_id)
            try:
                self._snapshot()
            finally:
                self._send_remote_logs()  # function is safe, wrapped with try/except block inside

    def run_continuous(self):
        with self:
            self._cpu_usage_logger.init_cycles()

            while not self._stop_event.is_set():
                self._state.init_new_cycle()

                try:
                    self._snapshot()
                except Exception:
                    logger.exception("Profiling run failed!")
                finally:
                    self._send_remote_logs()  # function is safe, wrapped with try/except block inside
                self._cpu_usage_logger.log_cycle()


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
        type=positive_integer,
        dest="frequency",
        default=DEFAULT_SAMPLING_FREQUENCY,
        help="Profiler frequency in Hz (default: %(default)s)",
    )
    parser.add_argument(
        "-d",
        "--profiling-duration",
        type=positive_integer,
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

    _add_profilers_arguments(parser)

    nodejs_options = parser.add_argument_group("NodeJS")
    nodejs_options.add_argument(
        "--nodejs-mode",
        dest="nodejs_mode",
        default="none",
        choices=["perf", "disabled", "none"],
        help="Select the NodeJS profiling mode: perf (run 'perf inject --jit' on perf results, to augment them"
        " with jitdump files of NodeJS processes, if present) or none (no runtime-specific profilers for NodeJS)",
    )

    nodejs_options.add_argument(
        "--no-nodejs",
        dest="nodejs_mode",
        action="store_const",
        const="disabled",
        default=True,
        help="Disable the runtime-profiling of NodeJS processes",
    )

    parser.add_argument(
        "--log-cpu-usage",
        action="store_true",
        default=False,
        help="Log CPU usage (per cgroup) on each profiling iteration. Works only when gProfiler runs as a container",
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
        type=positive_integer,
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
        "--log-rotate-max-size",
        action="store",
        type=positive_integer,
        dest="log_rotate_max_size",
        default=DEFAULT_LOG_MAX_SIZE,
    )
    logging_options.add_argument(
        "--log-rotate-backup-count",
        action="store",
        type=positive_integer,
        dest="log_rotate_backup_count",
        default=DEFAULT_LOG_BACKUP_COUNT,
    )
    logging_options.add_argument(
        "--dont-send-logs",
        action="store_false",
        dest="log_to_server",
        default=(os.getenv("GPROFILER_DONT_SEND_LOGS", None) is None),
        help="Disable sending logs to server",
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

    parser.add_argument(
        "--profile-api-version",
        action="store",
        dest="profile_api_version",
        default=None,
        choices=["v1"],
        help="Use a legacy API version to upload profiles to the Performance Studio",
    )

    parser.add_argument(
        "--disable-pidns-check",
        action="store_false",
        default=True,
        dest="pid_ns_check",
        help="Disable host PID NS check on startup",
    )

    parser.add_argument(
        "--disable-metrics-collection",
        action="store_false",
        default=True,
        dest="collect_metrics",
        help="Disable sending system metrics to the Performance Studio",
    )

    parser.add_argument(
        "--disable-metadata-collection",
        action="store_false",
        default=True,
        dest="collect_metadata",
        help="Disable sending system and cloud metadata to the Performance Studio",
    )

    args = parser.parse_args()

    if args.upload_results:
        if not args.server_token:
            parser.error("Must provide --token when --upload-results is passed")
        if not args.service_name:
            parser.error("Must provide --service-name when --upload-results is passed")

    if not args.upload_results and not args.output_dir:
        parser.error("Must pass at least one output method (--upload-results / --output-dir)")

    if args.dwarf_stack_size > 65528:
        parser.error("--perf-dwarf-stack-size maximum size is 65528")

    if args.perf_mode in ("dwarf", "smart") and args.frequency > 100:
        parser.error("--profiling-frequency|-f can't be larger than 100 when using --perf-mode 'smart' or 'dwarf'")

    if args.nodejs_mode == "perf" and args.perf_mode not in ("fp", "smart"):
        parser.error("--nodejs-mode perf requires --perf-mode 'fp' or 'smart'")

    return args


def _add_profilers_arguments(parser):
    registry = get_profilers_registry()
    for name, config in registry.items():
        arg_group = parser.add_argument_group(name)
        mode_var = f"{name.lower()}_mode"
        arg_group.add_argument(
            f"--{name.lower()}-mode",
            dest=mode_var,
            default=config.default_mode,
            help=config.profiler_mode_help,
            choices=config.possible_modes,
        )
        arg_group.add_argument(
            f"--no-{name.lower()}",
            action="store_const",
            const="disabled",
            dest=mode_var,
            default=True,
            help=config.disablement_help,
        )
        for arg in config.profiler_args:
            profiler_arg_kwargs = arg.get_dict()
            name = profiler_arg_kwargs.pop("name")
            arg_group.add_argument(name, **profiler_arg_kwargs)


def verify_preconditions(args):
    if not is_root():
        print("Must run gprofiler as root, please re-run.", file=sys.stderr)
        sys.exit(1)

    if args.pid_ns_check and not is_running_in_init_pid():
        print(
            "Please run me in the init PID namespace! In Docker, make sure you pass '--pid=host'."
            " In Kubernetes, add 'hostPID: true' in the Pod spec.\n"
            "You can disable this check with --disable-pidns-check.",
            file=sys.stderr,
        )
        sys.exit(1)

    if not grab_gprofiler_mutex():
        print("Could not acquire gProfiler's lock. Is it already running?", file=sys.stderr)
        sys.exit(1)

    if args.log_cpu_usage and get_run_mode_and_deployment_type()[0] not in ("k8s", "container"):
        # TODO: we *can* move into another cpuacct cgroup, to let this work also when run as a standalone
        # executable.
        print("--log-cpu-usage is available only when run as a container!")
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
    verify_preconditions(args)
    state = init_state()

    remote_logs_handler = RemoteLogsHandler() if args.log_to_server and args.upload_results else None
    global logger
    logger = initial_root_logger_setup(
        logging.DEBUG if args.verbose else logging.INFO,
        args.log_file,
        args.log_rotate_max_size,
        args.log_rotate_backup_count,
        remote_logs_handler,
    )

    setup_signals()
    reset_umask()
    # assume we run in the root cgroup (when containerized, that's our view)
    cpu_usage_logger = CpuUsageLogger(logger, "/", args.log_cpu_usage)

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

        if client is not None and remote_logs_handler is not None:
            remote_logs_handler.init_api_client(client)

        runtimes = {
            profiler_name.lower(): getattr(args, f"{profiler_name.lower()}_mode") not in ["none", "disabled"]
            for profiler_name in get_profilers_registry()
        }
        gprofiler = GProfiler(
            args.frequency,
            args.duration,
            args.output_dir,
            args.flamegraph,
            args.rotating_output,
            args.perf_mode,
            args.nodejs_mode,
            args.dwarf_stack_size,
            args.python_mode,
            args.pyperf_user_stacks_pages,
            runtimes,
            client,
            args.collect_metrics,
            args.collect_metadata,
            state,
            cpu_usage_logger,
            args.__dict__ if args.collect_metadata else None,
            not args.disable_container_names,
            args.profile_api_version,
            remote_logs_handler,
            args.php_process_filter,
        )
        logger.info("gProfiler initialized and ready to start profiling")
        if args.continuous:
            gprofiler.run_continuous()
        else:
            gprofiler.run_single()

    except KeyboardInterrupt:
        pass
    except Exception:
        logger.exception("Unexpected error occurred")

    cpu_usage_logger.log_run()


if __name__ == "__main__":
    main()
