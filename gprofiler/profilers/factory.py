from typing import TYPE_CHECKING, Any, List, Tuple, Union

from gprofiler.exceptions import SystemProfilerInitFailure
from gprofiler.log import get_logger_adapter
from gprofiler.profilers.profiler_base import NoopProfiler

if TYPE_CHECKING:
    from gprofiler.gprofiler_types import UserArgs
    from gprofiler.profilers.profiler_base import ProcessProfilerBase

from gprofiler.profilers.perf import SystemProfiler
from gprofiler.profilers.registry import get_profilers_registry

logger = get_logger_adapter(__name__)
COMMON_PROFILER_ARGUMENT_NAMES = ["frequency", "duration"]


def get_profilers(
    user_args: 'UserArgs', **profiler_init_kwargs: Any
) -> Tuple[Union['SystemProfiler', 'NoopProfiler'], List['ProcessProfilerBase']]:
    profilers_registry = get_profilers_registry()
    process_profilers_instances: List['ProcessProfilerBase'] = []
    system_profiler: Union['SystemProfiler', 'NoopProfiler'] = NoopProfiler()
    for profiler_name, profiler_config in profilers_registry.items():
        lower_profiler_name = profiler_name.lower()
        profiler_mode = user_args.get(f"{lower_profiler_name}_mode")
        if profiler_mode in ("none", "disabled"):
            continue

        profiler_kwargs = profiler_init_kwargs.copy()
        for key, value in user_args.items():
            if key.startswith(lower_profiler_name) or key in COMMON_PROFILER_ARGUMENT_NAMES:
                profiler_kwargs[key] = value
        try:
            profiler_instance = profiler_config.profiler_class(**profiler_kwargs)
        except Exception:
            if profiler_config.profiler_class is SystemProfiler:
                raise SystemProfilerInitFailure("Could not create the system profiler")
            logger.exception(f"Couldn't create the {profiler_name} profiler, continuing without it")
        else:
            if isinstance(profiler_instance, SystemProfiler):
                system_profiler = profiler_instance
            else:
                process_profilers_instances.append(profiler_instance)

    return system_profiler, process_profilers_instances
