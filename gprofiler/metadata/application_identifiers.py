#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import configparser
import os.path
import re
from abc import ABCMeta, abstractmethod
from typing import List, Optional

from granulate_utils.linux.ns import resolve_host_path
from psutil import NoSuchProcess, Process

from gprofiler.log import get_logger_adapter

_logger = get_logger_adapter(__name__)


_PYTHON_BIN_RE = re.compile(r"^python([23](\.\d{1,2})?)?$")


# Python does string interning so just initializing str() as a sentinel is not enough.
class StrSentinel(str):
    pass


_NON_AVAILABLE_ARG = StrSentinel()


def _is_python_m_proc(process: Process) -> bool:
    """
    Checks whether the process ran as "python -m ..." pattern.
    """
    args = process.cmdline()

    # Just to be on the safe side, module name must be run after the "-m" argument, python enforces it.
    if len(args) < 3:
        return False

    return _is_python_bin(args[0]) and args[1] == "-m"


def _is_python_bin(bin_name: str) -> bool:
    return _PYTHON_BIN_RE.match(os.path.basename(bin_name)) is not None


def _get_cli_arg_by_name(args: List[str], arg_name: str, check_for_equals_arg: bool = False) -> str:
    if arg_name in args:
        return args[args.index(arg_name) + 1]

    if check_for_equals_arg:
        for arg in args:
            arg_key, _, arg_val = arg.partition("=")
            if arg_key == arg_name:
                return arg_val

    return _NON_AVAILABLE_ARG


def _get_cli_arg_by_index(args: List[str], index: int) -> str:
    try:
        return args[index]
    except IndexError:
        return _NON_AVAILABLE_ARG


def _append_python_module_to_proc_wd(process: Process, module: str) -> str:
    # Convert module name to module path, for example a.b -> a/b.py
    if not module.endswith(".py"):
        module = module.replace(".", "/") + ".py"

    if os.path.isabs(module):
        return module

    return os.path.realpath(os.path.join(process.cwd(), module))


class _ApplicationIdentifier(metaclass=ABCMeta):
    @abstractmethod
    def get_application_name(self, process: Process) -> Optional[str]:
        pass


class _GunicornApplicationIdentifier(_ApplicationIdentifier):
    def get_application_name(self, process: Process) -> Optional[str]:
        # As of gunicorn documentation the WSGI module name most probably will come from the cmdline and not from the
        # config file / environment variables (they added the option to specify `wsgi_app`
        # in the config file only in version 20.1.0)

        if "gunicorn" != os.path.basename(
            _get_cli_arg_by_index(process.cmdline(), 0)
        ) and "gunicorn" != os.path.basename(_get_cli_arg_by_index(process.cmdline(), 1)):
            return None

        # wsgi app specification will come always as the last argument (if hasn't been specified config file)
        wsgi_app_spec = process.cmdline()[-1].split(":", maxsplit=1)[0]
        return f"gunicorn: {_append_python_module_to_proc_wd(process, wsgi_app_spec)}"


class _UwsgiApplicationIdentifier(_ApplicationIdentifier):
    @staticmethod
    def _find_wsgi_from_config_file(process: Process) -> Optional[str]:
        for arg in process.cmdline():
            if arg.endswith(".ini"):
                config_file = arg
                break
        else:
            return None

        if not os.path.isabs(config_file):
            config_file = os.path.join(process.cwd(), config_file)

        config = configparser.ConfigParser()
        config.read(resolve_host_path(process, config_file))
        try:
            # Note that `ConfigParser.get` doesn't act like `dict.get` and raises exceptions if section/option
            # isn't found.
            return config.get("uwsgi", "module")
        except (configparser.NoSectionError, configparser.NoOptionError):
            pass

        return None

    def get_application_name(self, process: Process) -> Optional[str]:
        if "uwsgi" != os.path.basename(_get_cli_arg_by_index(process.cmdline(), 0)):
            return None

        wsgi_arg = _get_cli_arg_by_name(process.cmdline(), "-w") or _get_cli_arg_by_name(
            process.cmdline(), "--wsgi-file"
        )
        if wsgi_arg is not _NON_AVAILABLE_ARG:
            return f"uwsgi: {_append_python_module_to_proc_wd(process, wsgi_arg)}"

        wsgi_config = self._find_wsgi_from_config_file(process)
        if wsgi_config is not None:
            return f"uwsgi: {_append_python_module_to_proc_wd(process, wsgi_config)}"

        _logger.warning(
            f"{self.__class__.__name__} Couldn't find uwsgi wsgi module, both from cmdline and from config file",
            cmdline=process.cmdline(),
            no_extra_to_server=True,
        )
        return None


class _CeleryApplicationIdentifier(_ApplicationIdentifier):
    @staticmethod
    def is_celery_process(process: Process) -> bool:
        if "celery" == os.path.basename(_get_cli_arg_by_index(process.cmdline(), 0)) or "celery" == os.path.basename(
            _get_cli_arg_by_index(process.cmdline(), 1)
        ):
            return True

        return _is_python_m_proc(process) and process.cmdline()[2] == "celery"

    def get_application_name(self, process: Process) -> Optional[str]:
        if not self.is_celery_process(process):
            return None

        app_name = _get_cli_arg_by_name(process.cmdline(), "-A") or _get_cli_arg_by_name(
            process.cmdline(), "--app", check_for_equals_arg=True
        )
        if app_name is None:
            _logger.warning(
                f"{self.__class__.__name__}: Couldn't find positional argument -A or --app for application indication",
                cmdline=process.cmdline(),
                no_extra_to_server=True,
            )
            return None

        return f"celery: {_append_python_module_to_proc_wd(process, app_name)}"


class _PySparkApplicationIdentifier(_ApplicationIdentifier):
    @staticmethod
    def _is_pyspark_process(process: Process) -> bool:
        # We're looking for pythonXX -m pyspark.daemon
        return _is_python_m_proc(process) and process.cmdline()[2] == "pyspark.daemon"

    def get_application_name(self, process: Process) -> Optional[str]:
        # TODO: detect application name from parent java native spark process.
        return "pyspark" if self._is_pyspark_process(process) else None


class _PythonModuleApplicationIdentifier(_ApplicationIdentifier):
    @staticmethod
    def is_python_app(process: Process) -> bool:
        if not _is_python_bin(_get_cli_arg_by_index(process.cmdline(), 0)):
            return False

        return _is_python_m_proc(process) or (len(process.cmdline()) >= 2 and process.cmdline()[1].endswith(".py"))

    def get_application_name(self, process: Process) -> Optional[str]:
        if not _is_python_bin(_get_cli_arg_by_index(process.cmdline(), 0)):
            return None

        module_arg = _get_cli_arg_by_name(process.cmdline(), "-m")
        if module_arg is not _NON_AVAILABLE_ARG:
            return f"python: -m {module_arg}"

        arg_1 = _get_cli_arg_by_index(process.cmdline(), 1)
        if arg_1.endswith(".py"):
            return f"python: {_append_python_module_to_proc_wd(process, arg_1)}"

        return None


class _JavaJarApplicationIdentifier(_ApplicationIdentifier):
    def get_application_name(self, process: Process) -> Optional[str]:
        if "java" != os.path.basename(_get_cli_arg_by_index(process.cmdline(), 0)) or "-jar" not in process.cmdline():
            return None

        return f"java: {_get_cli_arg_by_name(process.cmdline(), '-jar')}"


# Please note that the order matter, because the FIRST matching identifier will be used.
# so when adding new identifiers pay attention to the order.
_APPLICATION_IDENTIFIER = [
    _GunicornApplicationIdentifier(),
    _UwsgiApplicationIdentifier(),
    _CeleryApplicationIdentifier(),
    _PySparkApplicationIdentifier(),
    _PythonModuleApplicationIdentifier(),
    _JavaJarApplicationIdentifier(),
]


def get_application_name(pid: int) -> Optional[str]:
    """
    Tries to identify the application running in a given process, application identification is fully heuristic,
    heuristics are being made on each application type available differ from each other and those their
    "heuristic level".
    """
    try:
        process = Process(pid)

    # pid may be (-1) so we can catch also ValueError
    except (NoSuchProcess, ValueError):
        return None

    for identifier in _APPLICATION_IDENTIFIER:
        try:
            app_name = identifier.get_application_name(process)
            if app_name is not None:
                return app_name

        except NoSuchProcess:
            return None
        except Exception:
            _logger.exception(
                f"Application identifier {identifier} raised an exception while matching against process {process}"
            )
            continue

    return None


__all__ = ["get_application_name"]
