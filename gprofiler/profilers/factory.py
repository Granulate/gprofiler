import sys
from typing import TYPE_CHECKING, Any, List, Tuple, Union

from gprofiler.log import get_logger_adapter
from gprofiler.profilers.perf import SystemProfiler
from gprofiler.profilers.profiler_base import NoopProfiler
from gprofiler.profilers.registry import ProfilerConfig, get_runtimes_registry, get_sorted_profilers

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

    for runtime_class, runtime_config in get_runtimes_registry().items():
        runtime = runtime_config.runtime_name
        runtime_args_prefix = runtime.lower()
        runtime_mode = user_args.get(f"{runtime_args_prefix}_mode")
        if runtime_mode in ProfilerConfig.DISABLED_MODES:
            continue
        # select configs supporting requested runtime_mode or all configs in order of preference
        requested_configs: List[ProfilerConfig] = get_sorted_profilers(runtime_class)
        if runtime_mode != ProfilerConfig.ENABLED_MODE:
            requested_configs = [c for c in requested_configs if runtime_mode in c.get_active_modes()]
        # select profilers that support this architecture and profiling mode
        selected_configs: List[ProfilerConfig] = []
        for config in requested_configs:
            profiler_name = config.profiler_name
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
        mode_var = f"{runtime.lower()}_mode"
        runtime_arg_names: List[str] = [arg.dest for arg in runtime_config.common_arguments] + [mode_var]
        for profiler_config in selected_configs:
            profiler_name = profiler_config.profiler_name
            profiler_kwargs = profiler_init_kwargs.copy()
            profiler_arg_names = [arg.dest for arg in profiler_config.profiler_args]
            for key, value in user_args.items():
                if key in profiler_arg_names or key in runtime_arg_names or key in COMMON_PROFILER_ARGUMENT_NAMES:
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
                        f" {runtime} profiling with --{runtime_args_prefix}-mode=disabled to disable this profiler",
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
