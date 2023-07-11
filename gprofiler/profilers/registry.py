from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Type, Union

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
            arg.dest.startswith(runtime.lower()) for arg in profiler_arguments or []
        ), f"{profiler_name}: Profiler args dest must be prefixed with the profiler runtime name"
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
    """
    Get profiler modes supported for given runtime and available for current architecture.
    """
    arch = get_arch()
    added_modes: Set[str] = set()
    for config in (c for c in profilers_config[runtime] if arch in c.get_supported_archs()):
        added_modes.update(config.get_active_modes())
    initial_modes = [ProfilerConfig.ENABLED_MODE] if len(profilers_config[runtime]) > 1 else []
    return initial_modes + sorted(added_modes) + ProfilerConfig.DISABLED_MODES


def get_sorted_profilers(runtime: str) -> List[ProfilerConfig]:
    """
    Get all profiler configs registered for given runtime filtered for current architecture and sorted by preference.
    """
    arch = get_arch()
    profiler_configs = sorted(
        (c for c in profilers_config[runtime] if arch in c.get_supported_archs()),
        key=lambda c: (c.is_preferred, c.profiler_name),
        reverse=True,
    )
    return profiler_configs


def get_preferred_or_first_profiler(runtime: str) -> ProfilerConfig:
    return next(filter(lambda config: config.is_preferred, profilers_config[runtime]), profilers_config[runtime][0])


def get_profiler_arguments(runtime: str, profiler_name: str) -> List[ProfilerArgument]:
    """
    For now the common and specific profiler command-line arguments are defined together, at profiler
    registration.
    Once we implement a mechanism to hold runtime-wide options (including arguments), this function will be
    obsolete.
    """
    # Arguments can be distinguished by prefix of their dest variable name.
    # Group all profiler arguments and exclude those that are prefixed with other profiler names.
    runtime_lower = runtime.lower()
    other_profiler_prefixes = [
        f"{runtime_lower}_{config.profiler_name.lower().replace('-', '_')}"
        for config in profilers_config[runtime]
        if config.profiler_name != profiler_name
    ]
    all_runtime_args: List[ProfilerArgument] = [
        arg
        for config in profilers_config[runtime]
        for arg in config.profiler_args
        if arg.dest.startswith(runtime_lower)
    ]
    profiler_args = [arg for arg in all_runtime_args if False is any(map(arg.dest.startswith, other_profiler_prefixes))]
    return profiler_args
