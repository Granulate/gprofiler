import sys
from typing import TYPE_CHECKING, Any, List, Tuple, Union, cast

from gprofiler.log import get_logger_adapter
from gprofiler.metadata.system_metadata import get_arch
from gprofiler.platform import is_windows
from gprofiler.profilers.perf import SystemProfiler
from gprofiler.profilers.profiler_base import NoopProfiler, SystemProfilerBase
from gprofiler.profilers.registry import get_profilers_registry
from gprofiler.utils import is_profiler_disabled

if TYPE_CHECKING:
    from gprofiler.gprofiler_types import UserArgs
    from gprofiler.profilers.profiler_base import ProcessProfilerBase


logger = get_logger_adapter(__name__)
COMMON_PROFILER_ARGUMENT_NAMES = ["frequency", "duration", "min_duration"]


def get_profilers(
    user_args: "UserArgs", **profiler_init_kwargs: Any
) -> Tuple[Union["SystemProfilerBase", "NoopProfiler"], List["ProcessProfilerBase"]]:
    profiling_mode = user_args.get("profiling_mode")
    process_profilers_instances: List["ProcessProfilerBase"] = []
    system_profiler: Union["SystemProfilerBase", "NoopProfiler"] = NoopProfiler()

    # When custom event is specified, only use perf (SystemProfiler), disable all language profilers
    custom_event_mode = user_args.get("perf_event") is not None
    if custom_event_mode:
        logger.info(
            "Custom perf event mode enabled - disabling all language-specific profilers",
            event=user_args.get("perf_event"),
        )

    if profiling_mode != "none":
        arch = get_arch()
        for profiler_name, profiler_config in get_profilers_registry().items():
            lower_profiler_name = profiler_name.lower()
            profiler_mode = user_args.get(f"{lower_profiler_name}_mode")
            if is_profiler_disabled(cast(str, profiler_mode)):
                # Warn if Java-specific options are set but Java profiling is disabled
                if profiler_name == "Java" and user_args.get("java_collect_thread_names"):
                    logger.warning("--java-collect-thread-names is ignored because Java profiling is disabled")
                continue

            if profiling_mode not in profiler_config.supported_profiling_modes:
                logger.debug(f"Disabling {profiler_name} because it doesn't support profiling mode {profiling_mode!r}")
                continue

            supported_archs = (
                profiler_config.supported_windows_archs if is_windows() else profiler_config.supported_archs
            )
            if arch not in supported_archs:
                logger.warning(f"Disabling {profiler_name} because it doesn't support this architecture ({arch})")
                continue

            profiler_kwargs = profiler_init_kwargs.copy()
            for key, value in user_args.items():
                # Skip perf_event and perf_event_period as they're handled separately for custom events
                if key in ("perf_event", "perf_event_period", "perf_event_args"):
                    continue
                if key.startswith(lower_profiler_name) or key in COMMON_PROFILER_ARGUMENT_NAMES:
                    profiler_kwargs[key] = value

            # Add custom event parameters for SystemProfiler
            if profiler_name == SystemProfiler.name and custom_event_mode:
                profiler_kwargs["custom_event_name"] = user_args.get("perf_event")
                profiler_kwargs["custom_event_args"] = user_args.get("perf_event_args")
                profiler_kwargs["perf_period"] = user_args.get("perf_event_period")

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
                if isinstance(profiler_instance, SystemProfilerBase):
                    system_profiler = profiler_instance
                else:
                    # In custom event mode, skip all process profilers
                    if custom_event_mode:
                        logger.debug(f"Skipping {profiler_name} profiler in custom event mode")
                        continue
                    process_profilers_instances.append(profiler_instance)

    return system_profiler, process_profilers_instances
