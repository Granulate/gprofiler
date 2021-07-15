from typing import TYPE_CHECKING, Any, List, Tuple, Union

from gprofiler.log import get_logger_adapter
from gprofiler.profilers.profiler_base import NoopProfiler

if TYPE_CHECKING:
    from gprofiler.main import UserArgs
    from gprofiler.profilers.profiler_base import ProcessProfilerBase

from gprofiler.profilers.perf import SystemProfiler
from gprofiler.profilers.registry import get_profilers_registry

logger = get_logger_adapter(__name__)


def get_profilers(
    user_args: 'UserArgs', **profiler_init_kwargs: Any
) -> Tuple[Union['SystemProfiler', 'NoopProfiler'], List['ProcessProfilerBase']]:
    profilers_registry = get_profilers_registry()
    process_profilers_instances: List['ProcessProfilerBase'] = []
    system_profiler: Union['SystemProfiler', 'NoopProfiler'] = NoopProfiler()
    for profiler_name, profiler_config in profilers_registry.items():
        profiler_mode = user_args.get(f"{profiler_name.lower()}_mode")
        if profiler_mode == "none" or not user_args.get(profiler_name.lower(), True):
            continue
        try:
            profiler_instance = profiler_config.profiler_class(
                **profiler_init_kwargs, **user_args, profiler_mode=profiler_mode
            )
        except Exception:
            logger.exception(f"Couldn't create {profiler_name} profiler, continuing without this runtime profiler")
        else:
            if isinstance(profiler_instance, SystemProfiler):
                system_profiler = profiler_instance
            else:
                process_profilers_instances.append(profiler_instance)

    return system_profiler, process_profilers_instances
