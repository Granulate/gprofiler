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
import sys
import time
from logging import Logger
from pathlib import Path
from socket import gethostname
from tempfile import TemporaryDirectory
from threading import Event
from typing import Dict

import configargparse
from requests import RequestException, Timeout

from . import __version__
from . import merge
from .client import APIClient, APIError, GRANULATE_SERVER_HOST, DEFAULT_UPLOAD_TIMEOUT
from .java import JavaProfiler
from .perf import SystemProfiler
from .python import get_python_profiler
from .utils import is_root, run_process, get_iso8061_format_time, resource_path, log_system_info, TEMPORARY_STORAGE_PATH

logger: Logger

DEFAULT_LOG_FILE = "/var/log/gprofiler/gprofiler.log"
DEFAULT_LOG_MAX_SIZE = 1024 * 1024 * 5
DEFAULT_LOG_BACKUP_COUNT = 1

DEFAULT_PROFILING_DURATION = datetime.timedelta(seconds=60).seconds
DEFAULT_SAMPLING_FREQUENCY = 10
# by default - these match
DEFAULT_CONTINUOUS_MODE_INTERVAL = DEFAULT_PROFILING_DURATION


class GProfiler:
    def __init__(self, frequency: int, duration: int, output_dir: str, client: APIClient):
        self._frequency = frequency
        self._duration = duration
        self._output_dir = output_dir
        self._client = client
        self._stop_event = Event()
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        self._temp_storage_dir = TemporaryDirectory(dir=TEMPORARY_STORAGE_PATH)
        self.python_profiler = get_python_profiler(
            self._frequency, self._duration, self._stop_event, self._temp_storage_dir.name
        )
        self.java_profiler = JavaProfiler(
            self._frequency, self._duration, True, self._stop_event, self._temp_storage_dir.name
        )
        self.system_profiler = SystemProfiler(
            self._frequency, self._duration, self._stop_event, self._temp_storage_dir.name
        )

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def _generate_output_files(
        self, collapsed_data: str, local_start_time: datetime.datetime, local_end_time: datetime.datetime
    ) -> None:
        start_ts = get_iso8061_format_time(local_start_time)
        end_ts = get_iso8061_format_time(local_end_time)
        base_filename = os.path.join(self._output_dir, "profile_{}".format(end_ts))
        collapsed_path = base_filename + ".col"
        Path(collapsed_path).write_text(collapsed_data)

        flamegraph_path = base_filename + ".html"
        flamegraph = (
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
        Path(flamegraph_path).write_text(flamegraph)
        logger.info(f"Saved flamegraph to {flamegraph_path}")

    def start(self):
        self._stop_event.clear()

        for prof in (
            self.python_profiler,
            self.java_profiler,
            self.system_profiler,
        ):
            prof.start()

    def stop(self):
        logger.info("Stopping gprofiler...")
        self._stop_event.set()

        for prof in (
            self.python_profiler,
            self.java_profiler,
            self.system_profiler,
        ):
            prof.stop()

    def _snapshot(self):
        local_start_time = datetime.datetime.utcnow()
        monotonic_start_time = time.monotonic()

        java_future = self._executor.submit(self.java_profiler.snapshot)
        java_future.name = "java"
        python_future = self._executor.submit(self.python_profiler.snapshot)
        python_future.name = "python"
        system_future = self._executor.submit(self.system_profiler.snapshot)
        system_future.name = "system"

        process_perfs: Dict[int, Dict[str, int]] = {}
        for future in concurrent.futures.as_completed([java_future, python_future]):
            # if either of these fail - log it, and continue.
            try:
                process_perfs.update(future.result())
            except Exception:
                logger.exception(f"{future.name} profiling failed")

        local_end_time = local_start_time + datetime.timedelta(seconds=(time.monotonic() - monotonic_start_time))
        merged_result = merge.merge_perfs(system_future.result(), process_perfs)

        if self._output_dir:
            self._generate_output_files(merged_result, local_start_time, local_end_time)

        if self._client:
            try:
                self._client.submit_profile(local_start_time, local_end_time, gethostname(), merged_result)
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


def setup_logger(stream_level: int = logging.INFO, log_file_path: str = None):
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

    if log_file_path:
        os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_file_path, maxBytes=DEFAULT_LOG_MAX_SIZE, backupCount=DEFAULT_LOG_BACKUP_COUNT
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

    parser.add_argument("-v", "--verbose", action="store_true", default=False, dest="verbose")
    parser.add_argument("--log-file", action="store", type=str, dest="log_file", default=DEFAULT_LOG_FILE)

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

    return args


def verify_preconditions():
    if not is_root():
        print("Must run gprofiler as root, please re-run.", file=sys.stderr)
        sys.exit(1)


def main():
    args = parse_cmd_args()
    verify_preconditions()
    setup_logger(logging.DEBUG if args.verbose else logging.INFO, args.log_file)
    global logger  # silences flake8, who now knows that the "logger" global we refer to was initialized.

    try:
        logger.info(f"Running gprofiler (version {__version__})...")
        try:
            log_system_info()
        except Exception:
            logger.exception("Encountered an exception while getting basic system info")

        if args.output_dir:
            if not Path(args.output_dir).is_dir():
                logger.error("Output directory does not exist")
                sys.exit(1)

        if not os.path.exists(TEMPORARY_STORAGE_PATH):
            os.mkdir(TEMPORARY_STORAGE_PATH)

        try:
            client_kwargs = {}
            if "server_upload_timeout" in args:
                client_kwargs["upload_timeout"] = args.server_upload_timeout
            client = (
                APIClient(args.server_host, args.server_token, args.service_name, **client_kwargs)
                if args.upload_results
                else None
            )
        except APIError as e:
            logger.error(f"Server error: {e}")
            return
        except RequestException as e:
            logger.error(f"Failed to connect to server: {e}")
            return

        gprofiler = GProfiler(args.frequency, args.duration, args.output_dir, client)
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
