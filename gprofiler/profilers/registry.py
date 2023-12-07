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


@dataclass
class InternalArgument(ProfilerArgument):
    """
    Represents arguments internal to profiler, provided only by initialization code not on command line.
    Name should be empty and only dest field is meaningful, as it names the argument expected by profiler instance.
    """

    name: str = ""
    dest: str = ""
    internal: Optional[bool] = True

    def __post_init__(self) -> None:
        assert (
            set(self.get_dict()) == {"dest", "name", "internal"} and self.name == ""
        ), "InternalArgument doesn't use any other fields than dest"


class ProfilingRuntime:
    pass


@dataclass
class RuntimeConfig:
    runtime_name: str
    default_mode: str
    mode_help: Optional[str]
    disablement_help: Optional[str]
    common_arguments: List[ProfilerArgument]


class ProfilerConfig:
    def __init__(
        self,
        profiler_name: str,
        runtime_class: Type[ProfilingRuntime],
        is_preferred: bool,
        profiler_class: Any,
        possible_modes: List[str],
        supported_archs: List[str],
        supported_profiling_modes: List[str],
        supported_windows_archs: List[str] = None,
        arguments: List[ProfilerArgument] = None,
    ) -> None:
        self.profiler_name = profiler_name
        self.runtime_class = runtime_class
        self.is_preferred = is_preferred
        self.possible_modes = possible_modes
        self.supported_archs = supported_archs
        self.supported_windows_archs = supported_windows_archs if supported_windows_archs is not None else []
        self.profiler_args: List[ProfilerArgument] = arguments if arguments is not None else []
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


runtimes_config: Dict[Type[ProfilingRuntime], RuntimeConfig] = {}
profilers_config: Dict[Type[ProfilingRuntime], List[ProfilerConfig]] = defaultdict(list)


def register_runtime(
    runtime_name: str,
    default_mode: str = "enabled",
    mode_help: Optional[str] = None,
    disablement_help: Optional[str] = None,
    common_arguments: List[ProfilerArgument] = None,
) -> Any:
    if mode_help is None:
        mode_help = (
            f"Choose the mode for profiling {runtime_name} processes. '{default_mode}'"
            f" to profile them with the default method, or 'disabled' to disable {runtime_name}-specific profiling"
        )
    if disablement_help is None:
        disablement_help = f"Disable the runtime-profiling of {runtime_name} processes"

    def runtime_decorator(runtime_class: Type[ProfilingRuntime]) -> Any:
        assert runtime_class not in runtimes_config, f"Runtime {runtime_name} is already registered"
        assert all(
            arg.dest.startswith(runtime_name.lower()) for arg in common_arguments or []
        ), f"{runtime_name}: Runtime common args dest must be prefixed with the runtime name"

        runtimes_config[runtime_class] = RuntimeConfig(
            runtime_name,
            default_mode,
            mode_help,
            disablement_help,
            common_arguments if common_arguments is not None else [],
        )
        return runtime_class

    return runtime_decorator


def register_profiler(
    profiler_name: str,
    runtime_class: Type[ProfilingRuntime],
    possible_modes: List[str],
    supported_archs: List[str],
    supported_profiling_modes: List[str],
    is_preferred: bool = False,
    supported_windows_archs: Optional[List[str]] = None,
    profiler_arguments: Optional[List[ProfilerArgument]] = None,
) -> Any:
    # Add the legacy "none" value, which is replaced by "disabled"
    possible_modes.append("none")

    def profiler_decorator(profiler_class: Any) -> Any:
        assert profiler_name is not None, "Profiler name must be defined"
        assert (
            runtime_class in runtimes_config
        ), f"Profiler {profiler_name} refers to runtime {runtime_class}, which is not registered."
        runtime_name = runtimes_config[runtime_class].runtime_name
        assert profiler_name not in (
            config.profiler_name for profilers in profilers_config.values() for config in profilers
        ), f"{profiler_name} is already registered!"
        assert all(
            arg.dest.startswith(runtime_name.lower()) for arg in profiler_arguments or []
        ), f"{profiler_name}: Profiler args dest must be prefixed with the profiler runtime name"
        profilers_config[runtime_class] += [
            ProfilerConfig(
                profiler_name,
                runtime_class,
                is_preferred,
                profiler_class,
                possible_modes,
                supported_archs,
                supported_profiling_modes,
                supported_windows_archs,
                profiler_arguments,
            )
        ]
        profiler_class.name = profiler_name
        return profiler_class

    return profiler_decorator


def get_runtimes_registry() -> Dict[Type[ProfilingRuntime], RuntimeConfig]:
    return runtimes_config


def get_profilers_registry() -> Dict[Type[ProfilingRuntime], List[ProfilerConfig]]:
    return profilers_config


def get_profilers_by_name() -> Dict[str, ProfilerConfig]:
    return {config.profiler_name: config for configs in profilers_config.values() for config in configs}


def get_runtime_possible_modes(runtime_class: Type[ProfilingRuntime]) -> List[str]:
    """
    Get profiler modes supported for given runtime and available for current architecture.
    """
    arch = get_arch()
    added_modes: Set[str] = set()
    for config in (c for c in profilers_config[runtime_class] if arch in c.get_supported_archs()):
        added_modes.update(config.get_active_modes())
    if not added_modes:
        return []
    initial_modes = [ProfilerConfig.ENABLED_MODE] if len(profilers_config[runtime_class]) > 1 else []
    return initial_modes + sorted(added_modes) + ProfilerConfig.DISABLED_MODES


def get_sorted_profilers(runtime_class: Type[ProfilingRuntime]) -> List[ProfilerConfig]:
    """
    Get all profiler configs registered for given runtime filtered for current architecture and sorted by preference.
    """
    arch = get_arch()
    profiler_configs = sorted(
        (c for c in profilers_config[runtime_class] if arch in c.get_supported_archs()),
        key=lambda c: (c.is_preferred, c.profiler_name),
        reverse=True,
    )
    return profiler_configs
