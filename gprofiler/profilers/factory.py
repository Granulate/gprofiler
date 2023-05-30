import sys
from typing import TYPE_CHECKING, Any, List, Tuple, Union

from gprofiler.log import get_logger_adapter
from gprofiler.metadata.system_metadata import get_arch
from gprofiler.profilers.perf import SystemProfiler
from gprofiler.profilers.profiler_base import NoopProfiler
from gprofiler.profilers.registry import ProfilerConfig, get_profilers_registry, get_sorted_profilers

if TYPE_CHECKING:
    from gprofiler.gprofiler_types import UserArgs
    from gprofiler.profilers.profiler_base import ProcessProfilerBase


logger = get_logger_adapter(__name__)
COMMON_PROFILER_ARGUMENT_NAMES = ["frequency", "duration"]


def get_profilers(
    user_args: "UserArgs", **profiler_init_kwargs: Any
) -> Tuple[Union["SystemProfiler", "NoopProfiler"], List["ProcessProfilerBase"]]:
    profiling_mode = user_args.get("profiling_mode")
    process_profilers_instances: List["ProcessProfilerBase"] = []
    system_profiler: Union["SystemProfiler", "NoopProfiler"] = NoopProfiler()

    if profiling_mode == "none":
        return system_profiler, process_profilers_instances
    arch = get_arch()
    for runtime in get_profilers_registry():
        runtime_args_prefix = runtime.lower()
        runtime_mode = user_args.get(f"{runtime_args_prefix}_mode")
        if runtime_mode in ProfilerConfig.DISABLED_MODES:
            continue
        # select configs supporting requested runtime_mode or all configs in order of preference
        requested_configs: List[ProfilerConfig] = get_sorted_profilers(runtime)
        if runtime_mode != ProfilerConfig.ENABLED_MODE:
            requested_configs = [c for c in requested_configs if runtime_mode in c.get_active_modes()]
        # select profilers that support this architecture and profiling mode
        selected_configs: List[ProfilerConfig] = []
        for config in requested_configs:
            profiler_name = config.profiler_name
            if arch not in config.get_supported_archs() and len(requested_configs) == 1:
                logger.warning(f"Disabling {profiler_name} because it doesn't support this architecture ({arch})")
                continue
            if profiling_mode not in config.supported_profiling_modes:
                logger.warning(
                    f"Disabling {profiler_name} because it doesn't support profiling mode {profiling_mode!r}"
                )
                continue
            selected_configs.append(config)
        if not selected_configs:
            logger.warning(f"Disabling {runtime} profiling because no profilers were selected")
            continue
        # create instances of selected profilers one by one, select first that is ready
        ready_profiler = None
        for profiler_config in selected_configs:
            profiler_name = profiler_config.profiler_name
            profiler_kwargs = profiler_init_kwargs.copy()
            for key, value in user_args.items():
                if key.startswith(runtime_args_prefix) or key in COMMON_PROFILER_ARGUMENT_NAMES:
                    profiler_kwargs[key] = value
            try:
                profiler_instance = profiler_config.profiler_class(**profiler_kwargs)
                if profiler_instance.check_readiness():
                    ready_profiler = profiler_instance
                    break
            except Exception:
                if len(requested_configs) == 1:
                    logger.critical(
                        f"Couldn't create the {profiler_name} profiler for runtime {runtime}, not continuing."
                        f" Request different profiler for runtime with --{runtime_args_prefix}-mode, or disable"
                        f" {runtime} profiling with --no-{runtime_args_prefix} to disable this profiler",
                        exc_info=True,
                    )
                    sys.exit(1)
        if isinstance(ready_profiler, SystemProfiler):
            system_profiler = ready_profiler
        elif ready_profiler is not None:
            process_profilers_instances.append(ready_profiler)
        else:
            logger.warning(f"Disabling {runtime} profiling because no profilers were ready")
    return system_profiler, process_profilers_instances
