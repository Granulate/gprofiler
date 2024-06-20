#
# Copyright (C) 2022 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import concurrent.futures
import datetime
import logging
import logging.config
import logging.handlers
import os
import shutil
import signal
import sys
import time
import traceback
from pathlib import Path
from threading import Event
from types import FrameType, TracebackType
from typing import Iterable, List, Optional, Type, cast

import configargparse
import humanfriendly
from granulate_utils.linux.ns import is_running_in_init_pid
from granulate_utils.linux.process import is_process_running
from granulate_utils.metadata.cloud import get_aws_execution_env
from granulate_utils.metadata.databricks_client import DBXWebUIEnvWrapper, get_name_from_metadata
from psutil import NoSuchProcess, Process
from requests import RequestException, Timeout

from gprofiler import __version__
from gprofiler.client import (
    DEFAULT_API_SERVER_ADDRESS,
    DEFAULT_PROFILER_SERVER_ADDRESS,
    DEFAULT_UPLOAD_TIMEOUT,
    ProfilerAPIClient,
)
from gprofiler.consts import CPU_PROFILING_MODE
from gprofiler.containers_client import ContainerNamesClient
from gprofiler.diagnostics import log_diagnostics, set_diagnostics
from gprofiler.exceptions import APIError, NoProfilersEnabledError
from gprofiler.gprofiler_types import ProcessToProfileData, UserArgs, integers_list, positive_integer
from gprofiler.log import RemoteLogsHandler, initial_root_logger_setup
from gprofiler.merge import concatenate_from_external_file, concatenate_profiles, merge_profiles
from gprofiler.metadata import ProfileMetadata
from gprofiler.metadata.application_identifiers import ApplicationIdentifiers
from gprofiler.metadata.enrichment import EnrichmentOptions
from gprofiler.metadata.external_metadata import ExternalMetadataStaleError, read_external_metadata
from gprofiler.metadata.metadata_collector import get_current_metadata, get_static_metadata
from gprofiler.metadata.system_metadata import get_hostname, get_run_mode, get_static_system_info
from gprofiler.platform import is_linux, is_windows
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.factory import get_profilers
from gprofiler.profilers.profiler_base import NoopProfiler, ProcessProfilerBase, ProfilerInterface
from gprofiler.profilers.registry import get_profilers_registry
from gprofiler.state import State, init_state
from gprofiler.system_metrics import Metrics, NoopSystemMetricsMonitor, SystemMetricsMonitor, SystemMetricsMonitorBase
from gprofiler.usage_loggers import CgroupsUsageLogger, NoopUsageLogger, UsageLoggerInterface
from gprofiler.utils import (
    TEMPORARY_STORAGE_PATH,
    atomically_symlink,
    get_iso8601_format_time,
    grab_gprofiler_mutex,
    is_root,
    reset_umask,
    resource_path,
    run_process,
)
from gprofiler.utils.fs import escape_filename, mkdir_owned_root
from gprofiler.utils.proxy import get_https_proxy

if is_linux():
    from gprofiler.utils.linux import disable_core_files


logger: logging.LoggerAdapter

DEFAULT_LOG_FILE = "/var/log/gprofiler/gprofiler.log" if is_linux() else "./gprofiler.log"
DEFAULT_LOG_MAX_SIZE = 1024 * 1024 * 5
DEFAULT_LOG_BACKUP_COUNT = 1

DEFAULT_PID_FILE = "/var/run/gprofiler.pid"

DEFAULT_PROFILING_DURATION = datetime.timedelta(seconds=60).seconds
DEFAULT_SAMPLING_FREQUENCY = 11
DEFAULT_ALLOC_INTERVAL = "2mb"

DIAGNOSTICS_INTERVAL_S = 15 * 60

UPLOAD_FILE_SUBCOMMAND = "upload-file"

# 1 KeyboardInterrupt raised per this many seconds, no matter how many SIGINTs we get.
SIGINT_RATELIMIT = 0.5

last_signal_ts: Optional[float] = None


def sigint_handler(sig: int, frame: Optional[FrameType]) -> None:
    global last_signal_ts
    ts = time.monotonic()
    # no need for atomicity here: we can't get another SIGINT before this one returns.
    # https://www.gnu.org/software/libc/manual/html_node/Signals-in-Handler.html#Signals-in-Handler
    if last_signal_ts is None or ts > last_signal_ts + SIGINT_RATELIMIT:
        last_signal_ts = ts
        raise KeyboardInterrupt


class GProfiler:
    def __init__(
        self,
        *,
        output_dir: str,
        flamegraph: bool,
        rotating_output: bool,
        profiler_api_client: Optional[ProfilerAPIClient],
        collect_metrics: bool,
        collect_metadata: bool,
        enrichment_options: EnrichmentOptions,
        state: State,
        usage_logger: UsageLoggerInterface,
        user_args: UserArgs,
        duration: int,
        profile_api_version: str,
        profiling_mode: str,
        processes_to_profile: Optional[List[Process]],
        profile_spawned_processes: bool = True,
        remote_logs_handler: Optional[RemoteLogsHandler] = None,
        controller_process: Optional[Process] = None,
        external_metadata_path: Optional[Path] = None,
        heartbeat_file_path: Optional[Path] = None,
    ):
        self._output_dir = output_dir
        self._flamegraph = flamegraph
        self._rotating_output = rotating_output
        self._profiler_api_client = profiler_api_client
        self._state = state
        self._remote_logs_handler = remote_logs_handler
        self._profile_api_version = profile_api_version
        self._collect_metrics = collect_metrics
        self._collect_metadata = collect_metadata
        self._enrichment_options = enrichment_options
        self._static_metadata: Optional[ProfileMetadata] = None
        self._spawn_time = time.time()
        self._last_diagnostics = 0.0
        self._gpid = ""
        self._controller_process = controller_process
        self._duration = duration
        self._external_metadata_path = external_metadata_path
        self._heartbeat_file_path = heartbeat_file_path
        if self._collect_metadata:
            self._static_metadata = get_static_metadata(self._spawn_time, user_args, self._external_metadata_path)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        # TODO: we actually need 2 types of temporary directories.
        # 1. accessible by everyone - for profilers that run code in target processes, like async-profiler
        # 2. accessible only by us.
        # the latter can be root only. the former can not. we should do this separation so we don't expose
        # files unnecessarily.
        container_names_client = ContainerNamesClient() if self._enrichment_options.container_names else None
        self._profiler_state = ProfilerState(
            stop_event=Event(),
            storage_dir=TEMPORARY_STORAGE_PATH,
            profile_spawned_processes=profile_spawned_processes,
            insert_dso_name=bool(user_args.get("insert_dso_name")),
            profiling_mode=profiling_mode,
            container_names_client=container_names_client,
            processes_to_profile=processes_to_profile,
        )
        self.system_profiler, self.process_profilers = get_profilers(user_args, profiler_state=self._profiler_state)
        self._usage_logger = usage_logger
        if self._collect_metrics:
            self._system_metrics_monitor: SystemMetricsMonitorBase = SystemMetricsMonitor(
                self._profiler_state.stop_event
            )
        else:
            self._system_metrics_monitor = NoopSystemMetricsMonitor()

        if isinstance(self.system_profiler, NoopProfiler) and not self.process_profilers:
            raise NoProfilersEnabledError()

    @property
    def all_profilers(self) -> Iterable[ProfilerInterface]:
        yield from self.process_profilers
        yield self.system_profiler

    def __enter__(self) -> "GProfiler":
        self.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_ctb: Optional[TracebackType],
    ) -> None:
        self.stop()

    def _update_last_output(self, last_output_name: str, output_path: str) -> None:
        last_output = os.path.join(self._output_dir, last_output_name)
        prev_output = Path(last_output).resolve()
        if is_windows() and os.path.exists(last_output):
            os.remove(last_output)
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
        base_filename = os.path.join(self._output_dir, "profile_{}".format(escape_filename(end_ts)))
        collapsed_path = base_filename + ".col"
        Path(collapsed_path).write_text(collapsed_data, encoding="utf-8")
        stripped_collapsed_data = self._strip_extra_data(collapsed_data)

        # point last_profile.col at the new file; and possibly, delete the previous one.
        self._update_last_output("last_profile.col", collapsed_path)
        logger.info(f"Saved collapsed stacks to {collapsed_path}")

        if self._flamegraph:
            flamegraph_path = base_filename + ".html"
            flamegraph_data = (
                Path(resource_path("flamegraph/flamegraph_template.html"))
                .read_bytes()
                .replace(
                    b"{{{JSON_DATA}}}",
                    run_process(
                        [resource_path("burn"), "convert", "--type=folded"],
                        suppress_log=True,
                        stdin=stripped_collapsed_data.encode(),
                        stop_event=self._profiler_state.stop_event,
                        timeout=10,
                    ).stdout,
                )
                .replace(b"{{{START_TIME}}}", start_ts.encode())
                .replace(b"{{{END_TIME}}}", end_ts.encode())
            )
            Path(flamegraph_path).write_bytes(flamegraph_data)

            # point last_flamegraph.html at the new file; and possibly, delete the previous one.
            self._update_last_output("last_flamegraph.html", flamegraph_path)

            logger.info(f"Saved flamegraph to {flamegraph_path}")

    def _strip_extra_data(self, collapsed_data: str) -> str:
        """
        Strips the container names & application metadata index, if exists.
        """
        lines = []
        for line in collapsed_data.splitlines():
            if line.startswith("#"):
                continue
            if self._enrichment_options.application_metadata:
                line = line[line.find(";") + 1 :]
            lines.append(line[line.find(";") + 1 :])
        return "\n".join(lines)

    def start(self) -> None:
        self._profiler_state.stop_event.clear()
        self._system_metrics_monitor.start()

        for prof in list(self.all_profilers):
            try:
                prof.start()
            except Exception:
                # the SystemProfiler is handled separately - let the user run with '--perf-mode none' if they
                # wish so.
                if prof is self.system_profiler:
                    raise

                # others - are ignored, with a warning.
                logger.warning(f"Failed to start {prof.__class__.__name__}, continuing without it", exc_info=True)
                self.process_profilers.remove(cast(ProcessProfilerBase, prof))

    def stop(self) -> None:
        logger.info("Stopping ...")
        self._profiler_state.stop_event.set()
        self._system_metrics_monitor.stop()
        for prof in self.all_profilers:
            prof.stop()

    def _snapshot(self) -> None:
        local_start_time = datetime.datetime.utcnow()
        monotonic_start_time = time.monotonic()
        process_profilers_futures = []
        for prof in self.process_profilers:
            prof_future = self._executor.submit(prof.snapshot)
            prof_future.name = prof.name  # type: ignore # hack, add the profiler's name to the Future object
            process_profilers_futures.append(prof_future)
        system_future = self._executor.submit(self.system_profiler.snapshot)
        system_future.name = "system"  # type: ignore # hack, add the profiler's name to the Future object

        process_profiles: ProcessToProfileData = {}
        for future in concurrent.futures.as_completed(process_profilers_futures):
            # if either of these fail - log it, and continue.
            try:
                process_profiles.update(future.result())
            except Exception:
                future_name = future.name  # type: ignore # hack, add the profiler's name to the Future object
                logger.exception(f"{future_name} profiling failed")

        local_end_time = local_start_time + datetime.timedelta(seconds=(time.monotonic() - monotonic_start_time))

        try:
            system_result = system_future.result()
        except Exception:
            logger.critical(
                "Running perf failed; consider running gProfiler with '--perf-mode disabled' to avoid using perf",
            )
            raise
        metadata = (
            get_current_metadata(cast(ProfileMetadata, self._static_metadata))
            if self._collect_metadata
            else {"hostname": get_hostname()}
        )
        metadata.update({"profiling_mode": self._profiler_state.profiling_mode})
        metrics = self._system_metrics_monitor.get_metrics()

        try:
            external_app_metadata = read_external_metadata(self._external_metadata_path).application
        except ExternalMetadataStaleError:
            logger.warning("External metadata is stale, ignoring it")
            external_app_metadata = {}

        if NoopProfiler.is_noop_profiler(self.system_profiler):
            assert system_result == {}, system_result  # should be empty!
            merged_result = concatenate_profiles(
                process_profiles=process_profiles,
                container_names_client=self._profiler_state.container_names_client,
                enrichment_options=self._enrichment_options,
                metadata=metadata,
                metrics=metrics,
                external_app_metadata=external_app_metadata,
            )

        else:
            merged_result = merge_profiles(
                perf_pid_to_profiles=system_result,
                process_profiles=process_profiles,
                container_names_client=self._profiler_state.container_names_client,
                enrichment_options=self._enrichment_options,
                metadata=metadata,
                metrics=metrics,
                external_app_metadata=external_app_metadata,
            )

        if self._output_dir:
            self._generate_output_files(merged_result, local_start_time, local_end_time)

        if self._profiler_api_client:
            self._gpid = _submit_profile_logged(
                self._profiler_api_client,
                local_start_time,
                local_end_time,
                merged_result,
                self._profile_api_version,
                self._spawn_time,
                metrics,
                self._gpid,
            )

        if time.monotonic() - self._last_diagnostics > DIAGNOSTICS_INTERVAL_S:
            self._last_diagnostics = time.monotonic()
            log_diagnostics()

    def run_single(self) -> None:
        with self:
            # In case of single run mode, use the same id for run_id and cycle_id
            self._state.set_cycle_id(self._state.run_id)
            self._snapshot()
            self._state.set_cycle_id(None)

    def run_continuous(self) -> None:
        with self:
            self._usage_logger.init_cycles()

            while not self._profiler_state.stop_event.is_set():
                self._state.init_new_cycle()

                snapshot_start = time.monotonic()

                if self._heartbeat_file_path:
                    # --heart-beat flag
                    self._heartbeat_file_path.touch(mode=755, exist_ok=True)

                try:
                    self._snapshot()
                except Exception:
                    logger.exception("Profiling run failed!")
                self._usage_logger.log_cycle()

                # wait for one duration
                self._profiler_state.stop_event.wait(max(self._duration - (time.monotonic() - snapshot_start), 0))

                if self._controller_process is not None and not is_process_running(self._controller_process):
                    logger.info(f"Controller process {self._controller_process.pid} has exited; gProfiler stopping...")
                    break

            self._state.set_cycle_id(None)


def _submit_profile_logged(
    client: ProfilerAPIClient,
    start_time: datetime.datetime,
    end_time: datetime.datetime,
    profile: str,
    profile_api_version: Optional[str],
    spawn_time: float,
    metrics: "Metrics",
    gpid: str,
) -> str:
    try:
        response_dict = client.submit_profile(
            start_time,
            end_time,
            profile,
            profile_api_version,
            spawn_time,
            metrics,
            gpid,
        )
    except Timeout:
        logger.error("Upload of profile to server timed out.")
    except APIError as e:
        logger.error(f"Error occurred sending profile to server: {e}")
    except RequestException:
        logger.exception("Error occurred sending profile to server")
    else:
        logger.info("Successfully uploaded profiling data to the server")
        return response_dict.get("gpid", "")
    return ""


def send_collapsed_file_only(
    args: configargparse.Namespace,
    client: ProfilerAPIClient,
) -> None:
    spawn_time = time.time()
    gpid = ""
    metrics = NoopSystemMetricsMonitor().get_metrics()
    static_metadata: Optional[ProfileMetadata] = None
    if args.collect_metadata:
        static_metadata = get_static_metadata(spawn_time, args.__dict__, None)
    metadata = (
        get_current_metadata(cast(ProfileMetadata, static_metadata))
        if args.collect_metadata
        else {"hostname": get_hostname()}
    )
    local_start_time, local_end_time, merged_result = concatenate_from_external_file(
        args.file_path,
        metadata,
    )

    if local_start_time is None or local_end_time is None:
        assert (
            local_start_time is None and local_end_time is None
        ), "both start_time and end_time should be set, or none of them"
        local_start_time = local_end_time = datetime.datetime.utcnow()
    _submit_profile_logged(
        client,
        local_start_time,
        local_end_time,
        merged_result,
        args.profile_api_version,
        spawn_time,
        metrics,
        gpid,
    )


def copy_resources(path: Path) -> None:
    print(f"Copying gprofiler resources to {path}")
    shutil.copytree(resource_path(), path, dirs_exist_ok=True)


def parse_cmd_args() -> configargparse.Namespace:
    parser = configargparse.ArgumentParser(
        description="This is the gProfiler CLI documentation. You can access the general"
        " documentation at https://github.com/Granulate/gprofiler#readme.",
        auto_env_var_prefix="gprofiler_",
        add_config_file_help=True,
        add_env_var_help=False,
        default_config_files=["/etc/gprofiler/config.ini"],
    )
    parser.add_argument(
        "--pid-file", type=str, help="Override the pid-file location (default: %(default)s)", default=DEFAULT_PID_FILE
    )
    parser.add_argument("--config", is_config_file=True, help="Config file path")
    parser.add_argument(
        "-f",
        "--profiling-frequency",
        type=positive_integer,
        dest="frequency",
        help=f"Profiler frequency in Hz (default: {DEFAULT_SAMPLING_FREQUENCY}), to be used only in CPU profiling "
        f"(--mode=cpu, also the default mode)",
    )
    parser.add_argument(
        "-d",
        "--profiling-duration",
        type=positive_integer,
        dest="duration",
        default=DEFAULT_PROFILING_DURATION,
        help="Profiler duration per session in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--insert-dso-name",
        action="store_true",
        default=False,
        help="Include DSO name along function in call stack frames when available",
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
        "--mode",
        dest="profiling_mode",
        choices=["cpu", "allocation", "none"],
        default="cpu",
        help="Select gProfiler's profiling mode, default is %(default)s, available options are "
        "%(choices)s; allocation will profile only Java processes",
    )
    parser.add_argument(
        "--alloc-interval",
        dest="alloc_interval",
        type=str,
        help="Profiling interval to be used in allocation profiling, size in bytes (human friendly sizes supported,"
        " for example: '100kb'), to be used only in allocation profiling mode (--mode=allocation),"
        f" default: {DEFAULT_ALLOC_INTERVAL}",
    )

    parser.add_argument(
        "--rotating-output", action="store_true", default=False, help="Keep only the last profile result"
    )
    parser.add_argument(
        "--pids",
        dest="pids_to_profile",
        action="extend",
        default=None,
        type=integers_list,
        help="Comma separated list of processes that will be filtered to profile,"
        " given multiple times will append pids to one list",
    )

    _add_profilers_arguments(parser)

    spark_options = parser.add_argument_group("Spark")

    spark_options.add_argument(
        "--spark-sample-period",
        type=int,
        default=120,
        help="Deprecated! Removed in version 1.42.0",
    )

    spark_options.add_argument(
        "--collect-spark-metrics",
        default=False,
        action="store_true",
        help="Deprecated! Removed in version 1.42.0",
    )

    nodejs_options = parser.add_argument_group("NodeJS")
    nodejs_options.add_argument(
        "--nodejs-mode",
        dest="nodejs_mode",
        default="disabled",
        choices=["attach-maps", "perf", "disabled", "none"],
        help="Select the NodeJS profiling mode: attach-maps (generates perf-maps at runtime),"
        " perf (run 'perf inject --jit' on perf results, to augment them with jitdump files"
        " of NodeJS processes, if present) or disabled (no runtime-specific profilers for NodeJS)",
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
        "--log-usage",
        action="store_true",
        default=False,
        help="Log CPU & memory usage of gProfiler on each profiling iteration."
        " Currently works only if gProfiler runs as a container",
    )

    parser.add_argument(
        "-u",
        "--upload-results",
        action="store_true",
        default=False,
        help="Whether to upload the profiling results to the server",
    )

    parser.add_argument(
        "--dont-disable-core-files",
        action="store_false",
        dest="disable_core_files",
        help="Do not disable creation of coredumps for processes started by the profiler. By default, we disable,"
        " to refrain from generating core files which consume disk space, in case any of the executed profilers crash.",
    )

    subparsers = parser.add_subparsers(dest="subcommand")
    upload_file = subparsers.add_parser(UPLOAD_FILE_SUBCOMMAND)
    upload_file.add_argument(
        "--file-path",
        type=str,
        help="Path for the collapsed file to be uploaded",
        required=True,
    )
    for subparser in [parser, upload_file]:
        connectivity = subparser.add_argument_group("connectivity")
        connectivity.add_argument(
            "--server-host",
            default=DEFAULT_PROFILER_SERVER_ADDRESS,
            help="Server address for uploading profiles (default: %(default)s)",
        )
        connectivity.add_argument(
            "--api-server",
            default=DEFAULT_API_SERVER_ADDRESS,
            help="Server address for reporting logs and metrics (default: %(default)s)",
        )
        connectivity.add_argument(
            "--glogger-server",
            default=DEFAULT_API_SERVER_ADDRESS,
            dest="api_server",
            help="Deprecated alias for --api-server.",
        )
        connectivity.add_argument(
            "--server-upload-timeout",
            type=positive_integer,
            default=DEFAULT_UPLOAD_TIMEOUT,
            help="Timeout for upload requests to the server in seconds (default: %(default)s)",
        )
        connectivity.add_argument("--token", dest="server_token", help="Server token")
        connectivity.add_argument("--service-name", help="Service name")
        connectivity.add_argument(
            "--curlify-requests", help="Log cURL commands for HTTP requests (used for debugging)", action="store_true"
        )
        connectivity.add_argument(
            "--no-verify", help="Do not verify server certificates", action="store_false", dest="verify"
        )

    extract_resources = subparsers.add_parser("extract-resources")
    extract_resources.set_defaults(func=copy_resources)
    extract_resources.add_argument(
        "--resources-dest",
        dest="resources_dest",
        default=None,
        help="Path to which the resources will be extracted",
    )

    parser.add_argument("--version", action="version", version=__version__)
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
        action="store_false",
        dest="container_names",
        default=True,
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
        help="Use a legacy API version to upload profiles to the Performance Studio. This might disable some features.",
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

    parser.add_argument(
        "--disable-application-identifiers",
        action="store_false",
        default=True,
        dest="collect_appids",
        help="Disable collection of application identifiers",
    )

    parser.add_argument(
        "--app-id-args-filter",
        action="append",
        default=list(),
        dest="app_id_args_filters",
        help="A regex based filter for adding relevant arguments to the app id",
    )

    parser.add_argument(
        "--disable-application-metadata",
        action="store_false",
        default=True,
        dest="application_metadata",
        help="Disable collection of application metadata",
    )

    parser.add_argument(
        "--controller-pid",
        default=None,
        type=int,
        help="PID of the process that invoked gProfiler; if given and that process exits, gProfiler will exit"
        " as well",
    )

    parser.add_argument(
        "--external-metadata",
        default=None,
        type=str,
        help="Path to a file containing static & application metadata to be added to the profile. This option is"
        " used by other Granulate components to enrich the profile with additional metadata.",
    )

    parser.add_argument(
        "--databricks-job-name-as-service-name",
        action="store_true",
        dest="databricks_job_name_as_service_name",
        default=False,
        help="gProfiler will set service name to Databricks' job name on ephemeral clusters. It'll delay the beginning"
        " of the profiling due to repeated waiting for Spark's metrics server."
        ' service name format is: "databricks-job-<JOB-NAME>".'
        " Note that in any case that the job name is not available due to redaction,"
        " gProfiler will fallback to use the clusterName property.",
    )

    parser.add_argument(
        "--profile-spawned-processes",
        action="store_true",
        dest="profile_spawned_processes",
        default=False,
        help="gProfiler will listen for process spawn events, and will profile new processes that are spawned after the"
        " beginning of a session.",
    )

    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Log extra verbose information, making the debugging of gProfiler easier",
    )

    parser.add_argument(
        "--heartbeat-file",
        type=str,
        dest="heartbeat_file",
        default=None,
        help="Heartbeat file used to indicate gProfiler is functioning."
        "The file modification indicates the last snapshot time.",
    )

    args = parser.parse_args()

    args.perf_inject = args.nodejs_mode == "perf"
    args.perf_node_attach = args.nodejs_mode == "attach-maps"

    if args.profiling_mode == CPU_PROFILING_MODE:
        if args.alloc_interval:
            parser.error("--alloc-interval is only allowed in allocation profiling (--mode=allocation)")
        if not args.frequency:
            args.frequency = DEFAULT_SAMPLING_FREQUENCY
    elif args.profiling_mode == "allocation":
        if args.frequency:
            parser.error("-f|--frequency is only allowed in cpu profiling (--mode=cpu)")
        if not args.alloc_interval:
            args.alloc_interval = DEFAULT_ALLOC_INTERVAL
        args.frequency = humanfriendly.parse_size(args.alloc_interval, binary=True)

    if args.subcommand == UPLOAD_FILE_SUBCOMMAND:
        args.upload_results = True

    if args.subcommand == "extract-resources":
        args.extract_resources = True
    else:
        args.extract_resources = False

    if args.upload_results:
        if not args.server_token:
            parser.error("Must provide --token when --upload-results is passed")
        if not args.service_name and not args.databricks_job_name_as_service_name:
            parser.error("Must provide --service-name when --upload-results is passed")

    if not args.upload_results and not args.output_dir and not args.extract_resources:
        parser.error("Must pass at least one output method (--upload-results / --output-dir)")

    if args.extract_resources and args.resources_dest is None:
        parser.error("Must provide --resources-dest when extract-resources")

    if args.perf_dwarf_stack_size > 65528:
        parser.error("--perf-dwarf-stack-size maximum size is 65528")

    if args.profiling_mode == CPU_PROFILING_MODE and args.perf_mode in ("dwarf", "smart") and args.frequency > 100:
        parser.error("--profiling-frequency|-f can't be larger than 100 when using --perf-mode 'smart' or 'dwarf'")

    if args.nodejs_mode in ("perf", "attach-maps") and args.perf_mode not in ("fp", "smart"):
        parser.error("--nodejs-mode perf or attach-maps requires --perf-mode 'fp' or 'smart'")

    if args.profile_spawned_processes and args.pids_to_profile is not None:
        parser.error("--pids is not allowed when profiling spawned processes")

    return args


def _add_profilers_arguments(parser: configargparse.ArgumentParser) -> None:
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


def verify_preconditions(args: configargparse.Namespace, processes_to_profile: Optional[List[Process]]) -> None:
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

    try:
        if is_linux() and not grab_gprofiler_mutex():
            sys.exit(0)
    except Exception:
        traceback.print_exc()
        print(
            "Could not acquire gProfiler's lock due to an error. Are you running gProfiler in privileged mode?",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.log_usage and get_run_mode() not in ("k8s", "container"):
        # TODO: we *can* move into another cpuacct cgroup, to let this work also when run as a standalone
        # executable.
        print("--log-usage is available only when run as a container!", file=sys.stderr)
        sys.exit(1)

    if processes_to_profile is not None:
        if len(processes_to_profile) == 0:
            print("There aren't any alive processes provided via --pid PID list")
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


def log_system_info() -> None:
    system_info = get_static_system_info()
    logger.info(f"gProfiler Python version: {system_info.python_version}")
    logger.info(f"gProfiler deployment mode: {system_info.run_mode}")
    logger.info(f"Kernel uname release: {system_info.kernel_release}")
    logger.info(f"Kernel uname version: {system_info.kernel_version}")
    logger.info(f"Total CPUs: {system_info.processors}")
    logger.info(f"Total RAM: {system_info.memory_capacity_mb / 1024:.2f} GB")
    logger.info(f"Linux distribution: {system_info.os_name} | {system_info.os_release} | {system_info.os_codename}")
    logger.info(f"libc version: {system_info.libc_type}-{system_info.libc_version}")
    logger.info(f"Hostname: {system_info.hostname}")


def _should_send_logs(args: configargparse.Namespace) -> bool:
    # if:
    # * user didn't disable logs uploading, and
    # * we are uploading results, and
    # * protocol version is not v1 (v1 server does not have the logs endpoint)
    # then we should send logs!
    return bool(args.log_to_server and args.upload_results and args.profile_api_version != "v1")


def init_pid_file(pid_file: str) -> None:
    Path(pid_file).write_text(str(os.getpid()))


def setup_env(should_disable_core_files: bool, pid_file: str) -> None:
    """
    Set up gProfiler's environment.
    """
    setup_signals()
    reset_umask()

    if is_linux():
        if should_disable_core_files:
            disable_core_files()

        try:
            init_pid_file(pid_file)
        except Exception:
            logger.exception(f"Failed to write pid to '{pid_file}', continuing anyway")


def pids_to_processes(args: configargparse.Namespace) -> Optional[List[Process]]:
    if args.pids_to_profile is not None:
        processes_to_profile = []
        for pid in args.pids_to_profile:
            try:
                process = Process(pid)
                processes_to_profile.append(process)
            except NoSuchProcess:
                continue
        return processes_to_profile
    else:
        return None


def warn_about_deprecated_args(args: configargparse.Namespace) -> None:
    if args.spark_sample_period != 120:
        logger.warning("--spark-sample-period is deprecated and removed in version 1.42.0")

    if args.collect_spark_metrics:
        logger.warning("--collect-spark-metrics is deprecated and removed in version 1.42.0")


def main() -> None:
    args = parse_cmd_args()

    if hasattr(args, "func"):
        if args.subcommand == "extract-resources":
            args.func(args.resources_dest)
            return

    processes_to_profile = pids_to_processes(args)

    if is_windows() or get_aws_execution_env() == "AWS_ECS_FARGATE":
        args.perf_mode = "disabled"
        args.pid_ns_check = False

    if args.subcommand != UPLOAD_FILE_SUBCOMMAND:
        verify_preconditions(args, processes_to_profile)

    state = init_state()

    remote_logs_handler = (
        RemoteLogsHandler(args.api_server, args.server_token, args.service_name, args.verify)
        if _should_send_logs(args)
        else None
    )
    global logger
    logger = initial_root_logger_setup(
        logging.DEBUG if args.verbose else logging.INFO,
        args.log_file,
        args.log_rotate_max_size,
        args.log_rotate_backup_count,
        remote_logs_handler,
    )

    warn_about_deprecated_args(args)
    setup_env(args.disable_core_files, args.pid_file)

    # assume we run in the root cgroup (when containerized, that's our view)
    usage_logger = CgroupsUsageLogger(logger, "/") if args.log_usage else NoopUsageLogger()

    if args.databricks_job_name_as_service_name:
        # "databricks" will be the default name in case of failure with --databricks-job-name-as-service-name flag
        args.service_name = "databricks"
        dbx_web_ui_wrapper = DBXWebUIEnvWrapper(logger)
        dbx_metadata = dbx_web_ui_wrapper.all_props_dict
        if dbx_metadata is not None:
            service_suffix = get_name_from_metadata(dbx_metadata)
            if service_suffix is not None:
                args.service_name = f"databricks-{service_suffix}"

        if remote_logs_handler is not None:
            remote_logs_handler.update_service_name(args.service_name)

    try:
        logger.info(
            "Running gProfiler", version=__version__, commandline=" ".join(sys.argv[1:]), arguments=args.__dict__
        )
        if processes_to_profile is not None:
            logger.info("Target PIDs given by --pids", pids=[process.pid for process in processes_to_profile])
        if args.controller_pid is not None:
            try:
                controller_process: Optional[Process] = Process(args.controller_pid)
            except NoSuchProcess:
                logger.error("Give controller PID is not running!")
                sys.exit(1)
        else:
            controller_process = None

        external_metadata_path: Optional[Path] = None
        if args.external_metadata is not None:
            if args.subcommand == UPLOAD_FILE_SUBCOMMAND:
                logger.error(f"External metadata is not supported in {UPLOAD_FILE_SUBCOMMAND} mode!")
                sys.exit(1)

            external_metadata_path = Path(args.external_metadata)
            if not external_metadata_path.is_file():
                logger.error(f"External metadata file {args.external_metadata} does not exist!")
                sys.exit(1)

        heartbeat_file_path: Optional[Path] = None
        if args.heartbeat_file is not None:
            heartbeat_file_path = Path(args.heartbeat_file)

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

        mkdir_owned_root(TEMPORARY_STORAGE_PATH)

        try:
            client_kwargs = {}
            if "server_upload_timeout" in args:
                client_kwargs["upload_timeout"] = args.server_upload_timeout
            profiler_api_client = (
                ProfilerAPIClient(
                    token=args.server_token,
                    service_name=args.service_name,
                    server_address=args.server_host,
                    curlify_requests=args.curlify_requests,
                    hostname=get_hostname(),
                    verify=args.verify,
                    **client_kwargs,
                )
                if args.upload_results
                else None
            )
        except APIError as e:
            logger.error(f"Server error: {e}")
            sys.exit(1)
        except RequestException as e:
            proxy = get_https_proxy()
            proxy_str = repr(proxy) if proxy is not None else "none"
            logger.error(
                "Failed to connect to server. It might be blocked by your security rules / firewall,"
                " or you might require a proxy to access it from your environment?"
                f" Proxy used: {proxy_str}. Error: {e}"
            )
            sys.exit(1)

        if args.subcommand == UPLOAD_FILE_SUBCOMMAND:
            assert external_metadata_path is None  # not expecting it
            assert profiler_api_client is not None  # it's always initialized in upload-file mode
            send_collapsed_file_only(args, profiler_api_client)
            return

        enrichment_options = EnrichmentOptions(
            profile_api_version=args.profile_api_version,
            container_names=args.container_names,
            application_identifiers=args.collect_appids,
            application_identifier_args_filters=args.app_id_args_filters,
            application_metadata=args.application_metadata,
        )

        ApplicationIdentifiers.init(enrichment_options)
        set_diagnostics(args.diagnostics)
        gprofiler = GProfiler(
            output_dir=args.output_dir,
            flamegraph=args.flamegraph,
            rotating_output=args.rotating_output,
            profiler_api_client=profiler_api_client,
            collect_metrics=args.collect_metrics,
            collect_metadata=args.collect_metadata,
            enrichment_options=enrichment_options,
            state=state,
            usage_logger=usage_logger,
            user_args=args.__dict__,
            duration=args.duration,
            profile_api_version=args.profile_api_version,
            profiling_mode=args.profiling_mode,
            profile_spawned_processes=args.profile_spawned_processes,
            remote_logs_handler=remote_logs_handler,
            controller_process=controller_process,
            processes_to_profile=processes_to_profile,
            external_metadata_path=external_metadata_path,
            heartbeat_file_path=heartbeat_file_path,
        )
        logger.info("gProfiler initialized and ready to start profiling")
        if args.continuous:
            gprofiler.run_continuous()
        else:
            gprofiler.run_single()

    except KeyboardInterrupt:
        pass
    except NoProfilersEnabledError:
        logger.error("All profilers are disabled! Please enable at least one of them!")
        sys.exit(1)
    except ExternalMetadataStaleError:
        logger.error("External metadata file is stale! Please update it or disable external metadata, and try again.")
        sys.exit(1)
    except Exception:
        logger.exception("Unexpected error occurred")
        sys.exit(1)

    usage_logger.log_run()


if __name__ == "__main__":
    main()
