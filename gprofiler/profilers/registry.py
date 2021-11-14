from typing import Any, Callable, Dict, List, Optional, Sequence, Type, Union


class ProfilerArgument:
    # It would have been better to replace with a dataclass, but since we want to support Python 3.6 this is the best
    # alternative as we still need the get_dict method (which is not convenient to do with a namedtuple)
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
    ):
        self.name = name
        self.dest = dest
        self.help = help
        self.default = default
        self.action = action
        self.choices = choices
        self.type = type
        self.metavar = metavar

    def get_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


class ProfilerConfig:
    def __init__(
        self,
        profiler_mode_help: str,
        disablement_help: str,
        profiler_class,
        possible_modes: List[str],
        supported_archs: List[str],
        default_mode: str = "enabled",
        arguments: List[ProfilerArgument] = None,
    ):
        self.profiler_mode_help = profiler_mode_help
        self.possible_modes = possible_modes
        self.supported_archs = supported_archs
        self.default_mode = default_mode
        self.profiler_args: List[ProfilerArgument] = arguments if arguments is not None else []
        self.disablement_help = disablement_help
        self.profiler_class = profiler_class


profilers_config: Dict[str, ProfilerConfig] = {}


def register_profiler(
    profiler_name: str,
    default_mode: str,
    possible_modes: List[str],
    supported_archs: List[str],
    profiler_mode_argument_help: Optional[str] = None,
    profiler_arguments: Optional[List[ProfilerArgument]] = None,
    disablement_help: Optional[str] = None,
):
    if profiler_mode_argument_help is None:
        profiler_mode_argument_help = (
            f"Choose the mode for profiling {profiler_name} processes. '{default_mode}'"
            f" to profile them with the default method, or 'disabled' to disable {profiler_name}-specific profiling"
        )
    # Add the legacy "none" value, which is replaced by "disabled"
    possible_modes.append("none")
    if disablement_help is None:
        disablement_help = f"Disable the runtime-profiling of {profiler_name} processes"

    def profiler_decorator(profiler_class):
        assert profiler_name not in profilers_config, f"{profiler_name} is already registered!"
        profilers_config[profiler_name] = ProfilerConfig(
            profiler_mode_argument_help,
            disablement_help,
            profiler_class,
            possible_modes,
            supported_archs,
            default_mode,
            profiler_arguments,
        )
        profiler_class.name = profiler_name
        return profiler_class

    return profiler_decorator


def get_profilers_registry() -> Dict[str, ProfilerConfig]:
    return profilers_config
