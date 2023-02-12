import sys
from typing import TYPE_CHECKING, Any, List, Tuple, Union

from gprofiler.log import get_logger_adapter
from gprofiler.metadata.system_metadata import get_arch
from gprofiler.platform import is_windows
from gprofiler.profilers.perf import SystemProfiler
from gprofiler.profilers.profiler_base import NoopProfiler
from gprofiler.profilers.registry import get_profilers_registry

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

    if profiling_mode != "none":
        arch = get_arch()
        for profiler_name, profiler_config in get_profilers_registry().items():
            lower_profiler_name = profiler_name.lower()
            profiler_mode = user_args.get(f"{lower_profiler_name}_mode")
            if profiler_mode in ("none", "disabled"):
                continue

            supported_archs = (
                profiler_config.supported_windows_archs if is_windows() else profiler_config.supported_archs
            )
            if arch not in supported_archs:
                logger.warning(f"Disabling {profiler_name} because it doesn't support this architecture ({arch})")
                continue

            if profiling_mode not in profiler_config.supported_profiling_modes:
                logger.warning(
                    f"Disabling {profiler_name} because it doesn't support profiling mode {profiling_mode!r}"
                )
                continue

            profiler_kwargs = profiler_init_kwargs.copy()
            for key, value in user_args.items():
                if key.startswith(lower_profiler_name) or key in COMMON_PROFILER_ARGUMENT_NAMES:
                    profiler_kwargs[key] = value
            try:
                profiler_instance = profiler_config.profiler_class(**profiler_kwargs)
            except Exception:
                logger.critical(
                    f"Couldn't create the {profiler_name} profiler, not continuing."
                    f" Run with --no-{profiler_name.lower()} to disable this profiler",
                    exc_info=True,
                )
                sys.exit(1)
            else:
                if isinstance(profiler_instance, SystemProfiler):
                    system_profiler = profiler_instance
                else:
                    process_profilers_instances.append(profiler_instance)

    return system_profiler, process_profilers_instances
