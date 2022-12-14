from typing import Any, Callable, Dict, List, Optional, Sequence, Type, Union


class ProfilerArgument:
    # TODO convert to a dataclass
    def __init__(
        self,
        name: str,
        dest: str,
        help: Optional[str] = None,
        default: Any = None,
        action: Optional[str] = None,
        choices: Sequence[Any] = None,
        type: Union[Type, Callable[[str], Any]] = None,
        metavar: str = None,
        const: Any = None,
        nargs: str = None,
    ):
        self.name = name
        self.dest = dest
        self.help = help
        self.default = default
        self.action = action
        self.choices = choices
        self.type = type
        self.metavar = metavar
        self.const = const
        self.nargs = nargs

    def get_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


class ProfilerConfig:
    def __init__(
        self,
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
        self.profiler_mode_help = profiler_mode_help
        self.possible_modes = possible_modes
        self.supported_archs = supported_archs
        self.supported_windows_archs = supported_windows_archs if supported_windows_archs is not None else []
        self.default_mode = default_mode
        self.profiler_args: List[ProfilerArgument] = arguments if arguments is not None else []
        self.disablement_help = disablement_help
        self.profiler_class = profiler_class
        self.supported_profiling_modes = supported_profiling_modes


profilers_config: Dict[str, ProfilerConfig] = {}


def register_profiler(
    profiler_name: str,
    default_mode: str,
    possible_modes: List[str],
    supported_archs: List[str],
    supported_profiling_modes: List[str],
    supported_windows_archs: Optional[List[str]] = None,
    profiler_mode_argument_help: Optional[str] = None,
    profiler_arguments: Optional[List[ProfilerArgument]] = None,
    disablement_help: Optional[str] = None,
) -> Any:
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
        assert profiler_name not in profilers_config, f"{profiler_name} is already registered!"
        assert all(
            arg.dest.startswith(profiler_name.lower()) for arg in profiler_arguments or []
        ), f"{profiler_name}: Profiler args dest must be prefixed with the profiler name"
        profilers_config[profiler_name] = ProfilerConfig(
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
        profiler_class.name = profiler_name
        return profiler_class

    return profiler_decorator


def get_profilers_registry() -> Dict[str, ProfilerConfig]:
    return profilers_config
