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


def _is_python_bin(bin_name: str):
    return _PYTHON_BIN_RE.match(os.path.basename(bin_name)) is not None


class _ApplicationIdentifier(metaclass=ABCMeta):
    @abstractmethod
    def get_application_name(self, process: Process) -> Optional[str]:
        pass


_IP_PORT_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:\:\d{2,5})?$")  # Matches against (ip(:port))
_NON_AVAILABLE_ARG = str()


def _get_cli_arg_by_name(args: List[str], arg_name: str, check_for_equals_arg: bool = False) -> str:
    if arg_name in args:
        return args[args.index(arg_name) + 1]

    if check_for_equals_arg:
        for arg in args:
            arg_name, _, arg_val = arg.rpartition("=")
            if arg_val is not None:
                return arg_val

    return _NON_AVAILABLE_ARG


def _get_cli_arg_by_index(args: List[str], index: int) -> str:
    try:
        return args[index]
    except KeyError:
        return _NON_AVAILABLE_ARG


def _append_python_module_to_proc_wd(process: Process, module_name: str) -> str:
    return f'{process.cwd().replace("/", ".").strip(".")}.{module_name}'


class _GunicornApplicationIdentifier(_ApplicationIdentifier):
    def get_application_name(self, process: Process) -> Optional[str]:
        if "gunicorn" not in _get_cli_arg_by_index(process.cmdline(), 0) or "gunicorn" not in _get_cli_arg_by_index(
            process.cmdline(), 1
        ):
            return None

        # As of gunicorn documentation the WSGI module name most probably will come from the cmdline and not from the
        # config file / environment variables (they added the option to specify `wsgi_app` only in 20.1.0)
        for arg in process.cmdline():
            if ":" in arg:
                if _IP_PORT_RE.match(arg):
                    continue

                return f"gunicorn-{_append_python_module_to_proc_wd(process, arg)}"

        _logger.warning(
            f"GunicornApplicationSeparator: matched against process {process} but couldn't find WSGI module"
        )
        return None


class _UwsgiApplicationIdentifier(_ApplicationIdentifier):
    @staticmethod
    def _find_wsgi_from_config_file(process: Process) -> Optional[str]:
        config_file = None
        for arg in process.cmdline():
            if arg.endswith(".ini"):
                config_file = arg

        if config_file is None:
            return None

        if not os.path.isabs(config_file):
            config_file = os.path.join(process.cwd(), config_file)

        config = configparser.ConfigParser()
        config.read(resolve_host_path(process, config_file))
        try:
            return config.get("uwsgi", "module")
        except (configparser.NoSectionError, configparser.NoOptionError):
            pass

        return None

    def get_application_name(self, process: Process) -> Optional[str]:
        if "uwsgi" not in _get_cli_arg_by_index(process.cmdline(), 0):
            return None

        wsgi = _get_cli_arg_by_name(process.cmdline(), "-w")
        if wsgi is not None:
            return f"uwsgi-{_append_python_module_to_proc_wd(process, wsgi)}"

        wsgi = self._find_wsgi_from_config_file(process)
        if wsgi is not None:
            return f"uwsgi-{_append_python_module_to_proc_wd(process, wsgi)}"

        _logger.warning("Couldn't find uwsgi wsgi module, both from cmdline and from config file")
        return None


class _CeleryApplicationIdentifier(_ApplicationIdentifier):
    @staticmethod
    def is_celery_process(process: Process) -> bool:
        if _get_cli_arg_by_index(process.cmdline(), 0) == "celery":
            return True

        return len(process.cmdline()) >= 3 and ["-m", "celery"] == process.cmdline()[1:3]

    def get_application_name(self, process: Process) -> Optional[str]:
        if not self.is_celery_process(process):
            return None

        app_name = _get_cli_arg_by_name(process.cmdline(), "-A") or _get_cli_arg_by_name(
            process.cmdline(), "--app", check_for_equals_arg=True
        )
        if app_name is None:
            _logger.warning("Couldn't find positional argument -A or --app for application indication")
            return None

        return app_name


class _PySparkApplicationIdentifier(_ApplicationIdentifier):
    @staticmethod
    def _is_pyspark_process(process: Process) -> bool:
        # We're looking for pythonXX -m pyspark.daemon
        return (
            len(process.cmdline()) >= 3
            and "python" in process.cmdline()[0]
            and ["-m", "pyspark.daemon"] == process.cmdline()[1:3]
        )

    def get_application_name(self, process: Process) -> Optional[str]:
        return "PySpark" if self._is_pyspark_process(process) else None


class _PythonModuleApplicationIdentifier(_ApplicationIdentifier):
    @staticmethod
    def is_supported(process: Process) -> bool:
        if not _is_python_bin(process.cmdline()[0]):
            return False

        return (len(process.cmdline()) >= 3 and process.cmdline()[1] == "-m") or (
            len(process.cmdline()) >= 2 and process.cmdline()[1].endswith(".py")
        )

    def get_application_name(self, process: Process) -> Optional[str]:
        module_arg = _get_cli_arg_by_name(process.cmdline(), "-m")
        if module_arg is not None:
            return module_arg

        module_filename = process.cmdline()[1][:-3]  # Strip the ".py" (checked before)
        return f"GenericPython-{_append_python_module_to_proc_wd(process, module_filename)}"


class _JavaJarApplicationIdentifier(_ApplicationIdentifier):
    def get_application_name(self, process: Process) -> Optional[str]:
        if (
            "java" not in os.path.basename(_get_cli_arg_by_index(process.cmdline(), 0))
            or "-jar" not in process.cmdline()
        ):
            return None

        return f"JavaJar-{_get_cli_arg_by_name(process.cmdline(), '-jar')}"


# Please note that the order matter, because the FIRST matching separator will be used.
# so when adding new separators pay attention to the order.
_APPLICATION_SEPARATORS = [
    _GunicornApplicationIdentifier(),
    _UwsgiApplicationIdentifier(),
    _CeleryApplicationIdentifier(),
    _PySparkApplicationIdentifier(),
    _PythonModuleApplicationIdentifier(),
    _JavaJarApplicationIdentifier(),
]


def get_application_name(pid: int) -> Optional[str]:
    try:
        process = Process(pid)
    except NoSuchProcess:
        return None

    for separator in _APPLICATION_SEPARATORS:
        try:
            app_name = separator.get_application_name(process)
            if app_name is not None:
                return app_name

        except Exception:
            _logger.exception(
                f"Application separator {separator} raised an exception while matching against process {process}"
            )
            continue

    return None


__all__ = ["get_application_name"]
