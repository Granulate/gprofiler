import configparser
import os.path
import re
from abc import ABCMeta, abstractmethod
from typing import List, Optional

from granulate_utils.linux.ns import resolve_host_path
from psutil import NoSuchProcess, Process

from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)


_PYTHON_BIN_RE = re.compile(r"^python([23](\.\d{1,2})?)?$")


def _is_python_bin(bin_name: str):
    return _PYTHON_BIN_RE.match(os.path.basename(bin_name)) is not None


class ApplicationSeparator(metaclass=ABCMeta):
    @abstractmethod
    def is_supported(self, process: Process) -> bool:
        pass

    @abstractmethod
    def get_application_name(self, process: Process) -> Optional[str]:
        pass

    @property
    @abstractmethod
    def app_prefix(self) -> str:
        pass


_IP_PORT_RE = re.compile(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(?:\:\d{2,5})?$")  # Matches against (ip(:port))
_NON_AVAILABLE_ARG = str()


def get_cli_arg_by_name(args: List[str], arg_name: str, check_for_equals_arg: bool = False) -> str:
    if arg_name in args:
        return args[args.index(arg_name) + 1]

    if check_for_equals_arg:
        for arg in args:
            arg_name, _, arg_val = arg.rpartition("=")
            if arg_val is not None:
                return arg_val

    return _NON_AVAILABLE_ARG


def get_cli_arg_by_index(args: List[str], index: int) -> str:
    try:
        return args[index]
    except KeyError:
        return _NON_AVAILABLE_ARG


def append_python_module_to_proc_wd(process: Process, module_name: str) -> str:
    return f'{process.cwd().replace("/", ".").strip(".")}.{module_name}'


class GunicornApplicationSeparator(ApplicationSeparator):
    @property
    def app_prefix(self) -> str:
        return "gunicorn"

    def is_supported(self, process: Process) -> bool:
        # Either gunicorn (for example: /usr/local/bin/gunicorn) or python that runs gunicorn
        # (For example: /usr/local/bin/python /usr/local/bin/gunicorn)
        return "gunicorn" in get_cli_arg_by_index(process.cmdline(), 0) or "gunicorn" in get_cli_arg_by_index(
            process.cmdline(), 0
        )

    def get_application_name(self, process: Process) -> Optional[str]:
        # As of gunicorn documentation the WSGI module name most probably will come from the cmdline and not from the
        # config file / environment variables (they added the option to specify `wsgi_app` only in 20.1.0)
        for arg in process.cmdline():
            if ":" in arg:
                if _IP_PORT_RE.match(arg):
                    continue

                return append_python_module_to_proc_wd(process, arg)

        logger.warning(f"GunicornApplicationSeparator: matched against process {process} but couldn't find WSGI module")
        return None


class UwsgiApplicationSeparator(ApplicationSeparator):
    @property
    def app_prefix(self) -> str:
        return "uwsgi"

    def is_supported(self, process: Process) -> bool:
        return "uwsgi" in process.cmdline()[0]

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
        wsgi = get_cli_arg_by_name(process.cmdline(), "-w")
        if wsgi is not None:
            return append_python_module_to_proc_wd(process, wsgi)

        wsgi = self._find_wsgi_from_config_file(process)
        if wsgi is not None:
            return append_python_module_to_proc_wd(process, wsgi)

        logger.warning("Couldn't find uwsgi wsgi module, both from cmdline and from config file")
        return None


class CeleryApplicationSeparator(ApplicationSeparator):
    @property
    def app_prefix(self) -> str:
        return "celery"

    def is_supported(self, process: Process) -> bool:
        if get_cli_arg_by_index(process.cmdline(), 0) == "celery":
            return True

        return len(process.cmdline()) >= 3 and ["-m", "celery"] == process.cmdline()[1:3]

    def get_application_name(self, process: Process) -> Optional[str]:
        app_name = get_cli_arg_by_name(process.cmdline(), "-A") or get_cli_arg_by_name(
            process.cmdline(), "--app", check_for_equals_arg=True
        )
        if app_name is None:
            logger.warning("Couldn't find positional argument -A or --app for application indication")
            return None

        return app_name


class PySparkApplicationSeparator(ApplicationSeparator):
    @property
    def app_prefix(self) -> str:
        return "PySpark"

    def is_supported(self, process: Process) -> bool:
        # We're looking for pythonXX -m pyspark.daemon
        return (
            len(process.cmdline()) >= 3
            and "python" in process.cmdline()
            and ["-m", "pyspark.daemon"] == process.cmdline()[1:]
        )

    def get_application_name(self, process: Process) -> Optional[str]:
        return "PySpark"


class PythonModuleApplicationSeparator(ApplicationSeparator):
    @property
    def app_prefix(self) -> str:
        return "Python"

    def is_supported(self, process: Process) -> bool:
        if not _is_python_bin(process.cmdline()[0]):
            return False

        return (len(process.cmdline()) >= 3 and process.cmdline()[1] == "-m") or (
            len(process.cmdline()) >= 2 and process.cmdline()[1].endswith(".py")
        )

    def get_application_name(self, process: Process) -> Optional[str]:
        module_arg = get_cli_arg_by_name(process.cmdline(), "-m")
        if module_arg is not None:
            return module_arg

        module_filename = process.cmdline()[1][:-3]  # Strip the ".py" (checked before)
        return append_python_module_to_proc_wd(process, module_filename)


class JavaJarApplicationSeparator(ApplicationSeparator):
    def is_supported(self, process: Process) -> bool:
        return "java" in os.path.basename(process.cmdline()[0]) and "-jar" in process.cmdline()

    def get_application_name(self, process: Process) -> Optional[str]:
        return get_cli_arg_by_name(process.cmdline(), "-jar")

    @property
    def app_prefix(self) -> str:
        return "Java-Jar"


# Please note that the order matter, because the FIRST matching separator will be used.
# so when adding new separators pay attention to the order.
APPLICATION_SEPARATORS = [
    GunicornApplicationSeparator(),
    UwsgiApplicationSeparator(),
    CeleryApplicationSeparator(),
    PySparkApplicationSeparator(),
    PythonModuleApplicationSeparator(),
    JavaJarApplicationSeparator(),
]


def get_application_name(pid: int) -> Optional[str]:
    try:
        process = Process(pid)
    except NoSuchProcess:
        return None

    for separator in APPLICATION_SEPARATORS:
        try:
            if separator.is_supported(process):
                app_name = separator.get_application_name(process)
                prefix = separator.app_prefix
                return f"{prefix}-{app_name}"

        except Exception:
            logger.exception(
                f"Application separator {separator} raised an exception while matching against process {process}"
            )
            continue

    return None
