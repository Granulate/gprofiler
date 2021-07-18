from typing import Any, Dict, List, Optional, Type


class ProfilerArgument:
    def __init__(
        self,
        name: str,
        dest: str,
        help: str,
        default: Any = None,
        action: Optional[str] = None,
        choices: List[Any] = None,
        type: Type = None,
    ):
        self.name = name
        self.dest = dest
        self.help = help
        self.default = default
        self.action = action
        self.choices = choices
        self.type = type

    def get_dict(self) -> Dict[str, Any]:
        return {key: value for key, value in self.__dict__.items() if value is not None}


class ProfilerConfig:
    def __init__(
        self,
        profiler_mode_help: str,
        disablement_help: str,
        possible_modes: List[str] = None,
        default_mode: str = "enabled",
        arguments: List[ProfilerArgument] = None,
    ):
        self.profiler_mode_help: str = profiler_mode_help
        self.possible_modes: Optional[List[str]] = possible_modes
        self.default_mode: str = default_mode
        self.profiler_arguments: List[ProfilerArgument] = arguments if arguments is not None else []
        self.disablement_help = disablement_help


profilers_config: Dict[str, ProfilerConfig] = {}


def register_profiler(
    profiler_name: str,
    possible_modes: Optional[List] = None,
    default_mode="enabled",
    profiler_mode_argument_help: Optional[str] = None,
    profiler_arguments: Optional[List[ProfilerArgument]] = None,
    disablement_help: Optional[str] = None,
):
    if profiler_mode_argument_help is None:
        profiler_mode_argument_help = (
            f"Choose the mode for profiling {profiler_name} processes. 'enabled'"
            f" to profile them with the default method, or 'disabled' to disable {profiler_name}-specific profiling"
        )
    if possible_modes is None:
        possible_modes = ["enabled", "disabled", "none"]  # none is a legacy option
    else:
        # Add the legacy "none" value, which is replaced by "disabled"
        possible_modes.append("none")
    if disablement_help is None:
        disablement_help = f"Disable the runtime-profiling of {profiler_name} processes"

    def profiler_decorator(profiler_class):
        assert profiler_name not in profilers_config, f"{profiler_name} is already registered!"
        profilers_config[profiler_name] = ProfilerConfig(
            profiler_mode_argument_help, disablement_help, possible_modes, default_mode, profiler_arguments
        )
        return profiler_class

    return profiler_decorator


def get_profilers_registry():
    return profilers_config
