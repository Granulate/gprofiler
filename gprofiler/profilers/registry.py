from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Type, Union

from gprofiler.metadata.system_metadata import get_arch
from gprofiler.platform import is_windows


@dataclass
class ProfilerArgument:
    name: str
    dest: str
    help: Optional[str] = None
    default: Any = None
    action: Optional[str] = None
    choices: Union[Sequence[Any], None] = None
    type: Union[Type, Callable[[str], Any], None] = None
    metavar: Optional[str] = None
    const: Any = None
    nargs: Optional[str] = None

    def get_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in asdict(self).items() if value is not None}


class ProfilerConfig:
    def __init__(
        self,
        profiler_name: str,
        runtime: str,
        is_preferred: bool,
        profiler_mode_help: Optional[str],
        disablement_help: Optional[str],
        profiler_class: Any,
        possible_modes: List[str],
        supported_archs: List[str],
        supported_profiling_modes: List[str],
        supported_windows_archs: List[str] = None,
        default_mode: str = "enabled",
        arguments: List[ProfilerArgument] = None,
    ) -> None:
        self.profiler_name = profiler_name
        self.runtime = runtime
        self.is_preferred = is_preferred
        self.profiler_mode_help = profiler_mode_help
        self.possible_modes = possible_modes
        self.supported_archs = supported_archs
        self.supported_windows_archs = supported_windows_archs if supported_windows_archs is not None else []
        self.default_mode = default_mode
        self.profiler_args: List[ProfilerArgument] = arguments if arguments is not None else []
        self.disablement_help = disablement_help
        self.profiler_class = profiler_class
        self.supported_profiling_modes = supported_profiling_modes

    ENABLED_MODE: str = "enabled"
    DISABLED_MODES: List[str] = ["disabled", "none"]

    def get_active_modes(self) -> List[str]:
        return [
            mode
            for mode in self.possible_modes
            if mode not in ProfilerConfig.DISABLED_MODES and mode != ProfilerConfig.ENABLED_MODE
        ]

    def get_supported_archs(self) -> List[str]:
        return self.supported_windows_archs if is_windows() else self.supported_archs


profilers_config: Dict[str, List[ProfilerConfig]] = defaultdict(list)


def register_profiler(
    runtime: str,
    default_mode: str,
    possible_modes: List[str],
    supported_archs: List[str],
    supported_profiling_modes: List[str],
    profiler_name: Optional[str] = None,
    is_preferred: bool = False,
    supported_windows_archs: Optional[List[str]] = None,
    profiler_mode_argument_help: Optional[str] = None,
    profiler_arguments: Optional[List[ProfilerArgument]] = None,
    disablement_help: Optional[str] = None,
) -> Any:
    if profiler_name is None:
        profiler_name = runtime
    if profiler_mode_argument_help is None:
        profiler_mode_argument_help = (
            f"Choose the mode for profiling {profiler_name} processes. '{default_mode}'"
            f" to profile them with the default method, or 'disabled' to disable {profiler_name}-specific profiling"
        )
    # Add the legacy "none" value, which is replaced by "disabled"
    possible_modes.append("none")
    if disablement_help is None:
        disablement_help = f"Disable the runtime-profiling of {profiler_name} processes"

    def profiler_decorator(profiler_class: Any) -> Any:
        assert profiler_name is not None, "Profiler name must be defined"
        assert profiler_name not in (
            config.profiler_name for profilers in profilers_config.values() for config in profilers
        ), f"{profiler_name} is already registered!"
        assert all(
            arg.dest.startswith(profiler_name.lower()) for arg in profiler_arguments or []
        ), f"{profiler_name}: Profiler args dest must be prefixed with the profiler name"
        profilers_config[runtime] += [
            ProfilerConfig(
                profiler_name,
                runtime,
                is_preferred,
                profiler_mode_argument_help,
                disablement_help,
                profiler_class,
                possible_modes,
                supported_archs,
                supported_profiling_modes,
                supported_windows_archs,
                default_mode,
                profiler_arguments,
            )
        ]
        profiler_class.name = profiler_name
        return profiler_class

    return profiler_decorator


def get_profilers_registry() -> Dict[str, List[ProfilerConfig]]:
    return profilers_config


def get_profilers_by_name() -> Dict[str, ProfilerConfig]:
    return {config.profiler_name: config for configs in profilers_config.values() for config in configs}


def get_runtime_possible_modes(runtime: str) -> List[str]:
    possible_modes: List[str] = [ProfilerConfig.ENABLED_MODE] if len(profilers_config[runtime]) > 1 else []
    for config in profilers_config[runtime]:
        possible_modes += [m for m in config.get_active_modes() if m not in possible_modes]
    possible_modes += ProfilerConfig.DISABLED_MODES
    return possible_modes


def get_sorted_profilers(runtime: str) -> List[ProfilerConfig]:
    """
    Get profiler configs sorted by preference.
    """
    arch = get_arch()
    profiler_configs = sorted(
        profilers_config[runtime],
        key=lambda c: (arch in c.get_supported_archs(), c.is_preferred, c.profiler_name),
        reverse=True,
    )
    return profiler_configs
