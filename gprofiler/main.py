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
from types import FrameType, TracebackType
from typing import Iterable, Optional, Type, cast

import configargparse
from granulate_utils.linux.ns import is_running_in_init_pid
from granulate_utils.linux.process import is_process_running
from granulate_utils.metadata import Metadata
from psutil import NoSuchProcess, Process
from requests import RequestException, Timeout

from gprofiler import __version__
from gprofiler.client import DEFAULT_UPLOAD_TIMEOUT, GRANULATE_SERVER_HOST, APIClient
from gprofiler.containers_client import ContainerNamesClient
from gprofiler.exceptions import APIError, NoProfilersEnabledError, SystemProfilerInitFailure
from gprofiler.gprofiler_types import ProcessToProfileData, UserArgs, positive_integer
from gprofiler.log import RemoteLogsHandler, initial_root_logger_setup
from gprofiler.merge import concatenate_profiles, merge_profiles
from gprofiler.metadata.application_identifiers import set_enrichment_options
from gprofiler.metadata.enrichment import EnrichmentOptions
from gprofiler.metadata.metadata_collector import get_current_metadata, get_static_metadata
from gprofiler.metadata.system_metadata import get_hostname, get_run_mode, get_static_system_info
from gprofiler.profilers.factory import get_profilers
from gprofiler.profilers.profiler_base import NoopProfiler, ProcessProfilerBase, ProfilerInterface
from gprofiler.profilers.registry import get_profilers_registry
from gprofiler.state import State, init_state
from gprofiler.system_metrics import NoopSystemMetricsMonitor, SystemMetricsMonitor, SystemMetricsMonitorBase
from gprofiler.usage_loggers import CgroupsUsageLogger, NoopUsageLogger, UsageLoggerInterface
from gprofiler.utils import (
    TEMPORARY_STORAGE_PATH,
    TemporaryDirectoryWithMode,
    atomically_symlink,
    get_iso8601_format_time,
    grab_gprofiler_mutex,
    is_root,
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
        output_dir: str,
        flamegraph: bool,
        rotating_output: bool,
        client: Optional[APIClient],
        collect_metrics: bool,
        collect_metadata: bool,
        enrichment_options: EnrichmentOptions,
        state: State,
        usage_logger: UsageLoggerInterface,
        user_args: UserArgs,
        duration: int,
        profile_api_version: str,
        remote_logs_handler: Optional[RemoteLogsHandler] = None,
        controller_process: Optional[Process] = None,
    ):
        self._output_dir = output_dir
        self._flamegraph = flamegraph
        self._rotating_output = rotating_output
        self._client = client
        self._state = state
        self._remote_logs_handler = remote_logs_handler
        self._profile_api_version = profile_api_version
        self._collect_metrics = collect_metrics
        self._collect_metadata = collect_metadata
        self._enrichment_options = enrichment_options
        self._stop_event = Event()
        self._static_metadata: Optional[Metadata] = None
        self._spawn_time = time.time()
        self._gpid = ""
        self._controller_process = controller_process
        self._duration = duration
        if collect_metadata:
            self._static_metadata = get_static_metadata(spawn_time=self._spawn_time, run_args=user_args)
        self._executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        # TODO: we actually need 2 types of temporary directories.
        # 1. accessible by everyone - for profilers that run code in target processes, like async-profiler
        # 2. accessible only by us.
        # the latter can be root only. the former can not. we should do this separation so we don't expose
        # files unnecessarily.
        self._temp_storage_dir = TemporaryDirectoryWithMode(dir=TEMPORARY_STORAGE_PATH, mode=0o755)
        try:
            self.system_profiler, self.process_profilers = get_profilers(
                user_args,
                storage_dir=self._temp_storage_dir.name,
                stop_event=self._stop_event,
            )
        except SystemProfilerInitFailure:
            logger.exception("System profiler initialization has failed, exiting...")
            sys.exit(1)
        if self._enrichment_options.container_names:
            self._container_names_client: Optional[ContainerNamesClient] = ContainerNamesClient()
        else:
            self._container_names_client = None
        self._usage_logger = usage_logger
        if collect_metrics:
            self._system_metrics_monitor: SystemMetricsMonitorBase = SystemMetricsMonitor(self._stop_event)
        else:
            self._system_metrics_monitor = NoopSystemMetricsMonitor()

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
        Path(collapsed_path).write_text(collapsed_data)
        stripped_collapsed_data = self._strip_extra_data(collapsed_data)

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
                        [resource_path("burn"), "convert", "--type=folded"],
                        suppress_log=True,
                        stdin=stripped_collapsed_data.encode(),
                        stop_event=self._stop_event,
                        timeout=10,
                    ).stdout.decode(),
                )
                .replace("{{{START_TIME}}}", start_ts)
                .replace("{{{END_TIME}}}", end_ts)
            )
            Path(flamegraph_path).write_text(flamegraph_data)

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
        self._stop_event.clear()
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
        self._stop_event.set()
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
            logger.exception(
                "Running perf failed; consider running gProfiler with '--perf-mode disabled' to avoid using perf"
            )
            raise
        metadata = (
            get_current_metadata(cast(Metadata, self._static_metadata))
            if self._collect_metadata
            else {"hostname": get_hostname()}
        )
        metrics = self._system_metrics_monitor.get_metrics()
        if NoopProfiler.is_noop_profiler(self.system_profiler):
            assert system_result == {}, system_result  # should be empty!
            merged_result, total_samples = concatenate_profiles(
                process_profiles,
                self._container_names_client,
                self._enrichment_options,
                metadata,
                metrics,
            )

        else:
            merged_result, total_samples = merge_profiles(
                system_result,
                process_profiles,
                self._container_names_client,
                self._enrichment_options,
                metadata,
                metrics,
            )

        if self._output_dir:
            self._generate_output_files(merged_result, local_start_time, local_end_time)

        if self._client:
            try:
                response_dict = self._client.submit_profile(
                    local_start_time,
                    local_end_time,
                    merged_result,
                    total_samples,
                    self._profile_api_version,
                    self._spawn_time,
                    metrics,
                    self._gpid,
                )
                self._gpid = response_dict.get("gpid", "")
            except Timeout:
                logger.error("Upload of profile to server timed out.")
            except APIError as e:
                logger.error(f"Error occurred sending profile to server: {e}")
            except RequestException:
                logger.exception("Error occurred sending profile to server")
            else:
                logger.info("Successfully uploaded profiling data to the server")

    def _send_remote_logs(self) -> None:
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
        else:
            logger.debug("Successfully uploaded logs to the server")

    def run_single(self) -> None:
        with self:
            # In case of single run mode, use the same id for run_id and cycle_id
            self._state.set_cycle_id(self._state.run_id)
            try:
                self._snapshot()
            finally:
                self._send_remote_logs()  # function is safe, wrapped with try/except block inside

    def run_continuous(self) -> None:
        with self:
            self._usage_logger.init_cycles()

            while not self._stop_event.is_set():
                self._state.init_new_cycle()

                snapshot_start = time.monotonic()
                try:
                    self._snapshot()
                except Exception:
                    logger.exception("Profiling run failed!")
                finally:
                    self._send_remote_logs()  # function is safe, wrapped with try/except block inside
                self._usage_logger.log_cycle()

                # wait for one duration
                self._stop_event.wait(max(self._duration - (time.monotonic() - snapshot_start), 0))

                if self._controller_process is not None and not is_process_running(self._controller_process):
                    logger.info(f"Controller process {self._controller_process.pid} has exited; gProfiler stopping...")
                    break


def parse_cmd_args() -> configargparse.Namespace:
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
        default="disabled",
        choices=["perf", "disabled", "none"],
        help="Select the NodeJS profiling mode: perf (run 'perf inject --jit' on perf results, to augment them"
        " with jitdump files of NodeJS processes, if present) or disabled (no runtime-specific profilers for NodeJS)",
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
    parser.add_argument("--server-host", default=GRANULATE_SERVER_HOST, help="Server host (default: %(default)s)")
    parser.add_argument(
        "--server-upload-timeout",
        type=positive_integer,
        default=DEFAULT_UPLOAD_TIMEOUT,
        help="Timeout for upload requests to the server in seconds (default: %(default)s)",
    )
    parser.add_argument("--token", dest="server_token", help="Server token")
    parser.add_argument("--service-name", help="Service name")

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
        "--disable-application-identification",
        action="store_false",
        default=True,
        dest="identify_applications",
        help="Disable identification of applications by heuristics",
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

    args = parser.parse_args()

    args.perf_inject = args.nodejs_mode == "perf"

    if args.upload_results:
        if not args.server_token:
            parser.error("Must provide --token when --upload-results is passed")
        if not args.service_name:
            parser.error("Must provide --service-name when --upload-results is passed")

    if not args.upload_results and not args.output_dir:
        parser.error("Must pass at least one output method (--upload-results / --output-dir)")

    if args.perf_dwarf_stack_size > 65528:
        parser.error("--perf-dwarf-stack-size maximum size is 65528")

    if args.perf_mode in ("dwarf", "smart") and args.frequency > 100:
        parser.error("--profiling-frequency|-f can't be larger than 100 when using --perf-mode 'smart' or 'dwarf'")

    if args.nodejs_mode == "perf" and args.perf_mode not in ("fp", "smart"):
        parser.error("--nodejs-mode perf requires --perf-mode 'fp' or 'smart'")

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


def verify_preconditions(args: configargparse.Namespace) -> None:
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
        sys.exit(0)

    if args.log_usage and get_run_mode() not in ("k8s", "container"):
        # TODO: we *can* move into another cpuacct cgroup, to let this work also when run as a standalone
        # executable.
        print("--log-usage is available only when run as a container!", file=sys.stderr)
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


def main() -> None:
    args = parse_cmd_args()
    verify_preconditions(args)
    state = init_state()

    remote_logs_handler = RemoteLogsHandler() if _should_send_logs(args) else None
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
    usage_logger = CgroupsUsageLogger(logger, "/") if args.log_usage else NoopUsageLogger()

    try:
        logger.info(f"Running gprofiler (version {__version__}), commandline: {' '.join(sys.argv[1:])!r}")

        if args.controller_pid is not None:
            try:
                controller_process: Optional[Process] = Process(args.controller_pid)
            except NoSuchProcess:
                logger.error("Give controller PID is not running!")
                sys.exit(1)
        else:
            controller_process = None

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
            sys.exit(1)
        except RequestException as e:
            logger.error(
                "Failed to connect to server. It might be blocked by your security rules / firewall,"
                f" or you might require a proxy to access it from your environment? {e}"
            )
            sys.exit(1)

        if client is not None and remote_logs_handler is not None:
            remote_logs_handler.init_api_client(client)

        enrichment_options = EnrichmentOptions(
            profile_api_version=args.profile_api_version,
            container_names=args.container_names,
            application_identifiers=args.identify_applications,
            application_identifier_args_filters=args.app_id_args_filters,
            application_metadata=args.application_metadata,
        )

        set_enrichment_options(enrichment_options)

        gprofiler = GProfiler(
            args.output_dir,
            args.flamegraph,
            args.rotating_output,
            client,
            args.collect_metrics,
            args.collect_metadata,
            enrichment_options,
            state,
            usage_logger,
            args.__dict__,
            args.duration,
            args.profile_api_version,
            remote_logs_handler,
            controller_process,
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
    except Exception:
        logger.exception("Unexpected error occurred")
        sys.exit(1)

    usage_logger.log_run()


if __name__ == "__main__":
    main()
