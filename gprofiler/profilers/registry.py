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
        dict_argument_names = ["help", "default", "action", "choices", "dest", "type"]
        argument_dict = {}
        for argument_name in dict_argument_names:
            argument_value = getattr(self, argument_name)
            if argument_value is not None:
                argument_dict[argument_name] = argument_value
        return argument_dict


class ProfilerConfig:
    def __init__(
        self,
        profiler_mode_help: str,
        possible_modes: List[str] = None,
        default_mode: str = "auto",
        arguments: List[ProfilerArgument] = None,
    ):
        self.profiler_mode_help: str = profiler_mode_help
        self.possible_modes: Optional[List[str]] = possible_modes
        self.default_mode: str = default_mode
        self.profiler_arguments: List[ProfilerArgument] = arguments if arguments is not None else []


profilers_config: Dict[str, ProfilerConfig] = {}


def register_profiler(
    profiler_name: str,
    possible_modes: Optional[List] = None,
    default_mode="enabled",
    profiler_mode_argument_help: Optional[str] = None,
    profiler_arguments: Optional[List[ProfilerArgument]] = None,
):
    if profiler_mode_argument_help is None:
        profiler_mode_argument_help = (
            f"Choose the mode for profiling {profiler_name} processes. 'enabled'"
            f" to automatically profile them, or 'disabled' to disable {profiler_name} profiling"
        )
    if possible_modes is None:
        possible_modes = ["enabled", "disabled"]

    def profiler_decorator(profiler_class):
        global profilers_config
        profilers_config[profiler_name] = ProfilerConfig(
            profiler_mode_argument_help, possible_modes, default_mode, profiler_arguments
        )
        return profiler_class

    return profiler_decorator


def get_profilers_registry():
    return profilers_config
