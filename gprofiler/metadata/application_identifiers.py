#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from __future__ import annotations

import configparser
import functools
import os.path
import re
from typing import Dict, List, Optional, TextIO, Tuple

from granulate_utils.linux.ns import resolve_host_path, resolve_proc_root_links
from psutil import NoSuchProcess, Process

from gprofiler.log import get_logger_adapter
from gprofiler.metadata.base_application_identifier import _ApplicationIdentifier
from gprofiler.metadata.enrichment import EnrichmentOptions
from gprofiler.platform import is_linux

if is_linux():
    from gprofiler.metadata.application_identifiers_java import (
        _JavaJarApplicationIdentifier,
        _JavaSparkApplicationIdentifier,
    )
    from gprofiler.profilers.java import JattachJcmdRunner
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


def _get_cli_arg_by_name(
    args: List[str], arg_name: str, check_for_equals_arg: bool = False, check_for_short_prefix_arg: bool = False
) -> str:
    if arg_name in args:
        return args[args.index(arg_name) + 1]

    if check_for_equals_arg:
        for arg in args:
            arg_key, _, arg_val = arg.partition("=")
            if arg_key == arg_name:
                return arg_val

    if check_for_short_prefix_arg:
        for arg in args:
            if arg.startswith(arg_name):
                return arg[len(arg_name) :]

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

    return _append_file_to_proc_wd(process, module)


def _append_file_to_proc_wd(process: Process, file_path: str) -> str:
    # if file_path is absolute, then the process.cwd() is removed.
    file_path = os.path.join(process.cwd(), file_path)
    proc_root = f"/proc/{process.pid}/root"
    resolved = resolve_proc_root_links(proc_root, file_path)
    assert resolved.startswith(proc_root), resolved
    return resolved[len(proc_root) :]


class _GunicornApplicationIdentifierBase(_ApplicationIdentifier):
    def gunicorn_to_app_id(self, wsgi_app_spec: str, process: Process) -> str:
        wsgi_app_file = wsgi_app_spec.split(":", maxsplit=1)[0]
        return f"gunicorn: {wsgi_app_spec} ({_append_python_module_to_proc_wd(process, wsgi_app_file)})"


class _GunicornApplicationIdentifier(_GunicornApplicationIdentifierBase):
    def get_app_id(self, process: Process) -> Optional[str]:
        # As of gunicorn documentation the WSGI module name most probably will come from the cmdline and not from the
        # config file / environment variables (they added the option to specify `wsgi_app`
        # in the config file only in version 20.1.0)

        if "gunicorn" != os.path.basename(
            _get_cli_arg_by_index(process.cmdline(), 0)
        ) and "gunicorn" != os.path.basename(_get_cli_arg_by_index(process.cmdline(), 1)):
            return None

        # wsgi app specification will come always as the last argument (if hasn't been specified config file)
        return self.gunicorn_to_app_id(process.cmdline()[-1], process)


class _GunicornTitleApplicationIdentifier(_GunicornApplicationIdentifierBase):
    """
    This generates appids from gunicorns that use setproctitle to change their name,
    and thus appear like "gunicorn: worker [my.wsgi:app]".
    See:
        setproctitle():
        https://github.com/benoitc/gunicorn/blob/60d0474a6f5604597180f435a6a03b016783885b/gunicorn/util.py#L50
        title format:
        https://github.com/benoitc/gunicorn/blob/60d0474a6f5604597180f435a6a03b016783885b/gunicorn/arbiter.py#L580
    """

    _GUNICORN_TITLE_PROC_NAME = re.compile(r"^gunicorn: (?:(?:master)|(?:worker)) \[([^\]]*)\]$")

    def get_app_id(self, process: Process) -> Optional[str]:
        cmdline = process.cmdline()
        # There should be one entry in the commandline, starting with "gunicorn: ",
        # and the rest should be empty strings per Process.cmdline() (I suppose that setproctitle
        # zeros out the arguments array).
        if _get_cli_arg_by_index(cmdline, 0).startswith("gunicorn: ") and len(list(filter(lambda s: s, cmdline))) == 1:
            m = self._GUNICORN_TITLE_PROC_NAME.match(cmdline[0])
            if m is not None:
                return self.gunicorn_to_app_id(m.group(1), process)
        return None


class _UwsgiApplicationIdentifier(_ApplicationIdentifier):
    # separated so that we can mock it easily in the tests
    @staticmethod
    def _open_uwsgi_config_file(process: Process, config_file: str) -> TextIO:
        return open(resolve_host_path(process, os.path.join(process.cwd(), config_file)))

    @classmethod
    def _find_wsgi_from_config_file(cls, process: Process) -> Tuple[Optional[str], Optional[str]]:
        cmdline = process.cmdline()

        config_file = _get_cli_arg_by_name(cmdline, "--ini", check_for_equals_arg=True)
        if config_file is _NON_AVAILABLE_ARG:
            config_file = _get_cli_arg_by_name(cmdline, "--ini-paste", check_for_equals_arg=True)
        if config_file is _NON_AVAILABLE_ARG:
            config_file = _get_cli_arg_by_name(cmdline, "--ini-paste-logged", check_for_equals_arg=True)
        if config_file is _NON_AVAILABLE_ARG:
            # uwsgi also accepts just .ini files without any preceeding arg (checked on version 2.0.20)
            inis = list(filter(lambda arg: arg.endswith(".ini"), cmdline))
            if len(inis) > 0:
                if len(inis) > 1:
                    _logger.warning(
                        f"{cls.__name__} Couldn't find uwsgi wsgi module, both from cmdline and from config file",
                        cmdline=process.cmdline(),
                        no_extra_to_server=True,
                    )
                else:
                    config_file = inis[0]

        if config_file is _NON_AVAILABLE_ARG:
            return None, None

        config = configparser.ConfigParser(strict=False)
        with cls._open_uwsgi_config_file(process, config_file) as f:
            config.read_file(f)
        try:
            # Note that `ConfigParser.get` doesn't act like `dict.get` and raises exceptions if section/option
            # isn't found.
            return config_file, config.get("uwsgi", "module")
        except (configparser.NoSectionError, configparser.NoOptionError):
            pass

        return config_file, None

    def get_app_id(self, process: Process) -> Optional[str]:
        if "uwsgi" != os.path.basename(_get_cli_arg_by_index(process.cmdline(), 0)):
            return None

        wsgi_arg = _get_cli_arg_by_name(process.cmdline(), "-w") or _get_cli_arg_by_name(
            process.cmdline(), "--wsgi-file", check_for_equals_arg=True
        )
        if wsgi_arg is not _NON_AVAILABLE_ARG:
            return f"uwsgi: {wsgi_arg} ({_append_python_module_to_proc_wd(process, wsgi_arg)})"

        wsgi_config_file, wsgi_config = self._find_wsgi_from_config_file(process)
        if wsgi_config is not None:
            return f"uwsgi: {wsgi_config_file} ({_append_python_module_to_proc_wd(process, wsgi_config)})"

        _logger.warning(
            f"{self.__class__.__name__} Couldn't find uwsgi wsgi module, both from cmdline and from config file",
            cmdline=process.cmdline(),
            no_extra_to_server=True,
        )
        if wsgi_config_file is not None:
            return f"uwsgi: {wsgi_config_file}"
        else:
            return "uwsgi: ini file / wsgi module not found"


class _CeleryApplicationIdentifier(_ApplicationIdentifier):
    @staticmethod
    def is_celery_process(process: Process) -> bool:
        if "celery" == os.path.basename(_get_cli_arg_by_index(process.cmdline(), 0)) or "celery" == os.path.basename(
            _get_cli_arg_by_index(process.cmdline(), 1)
        ):
            return True

        return _is_python_m_proc(process) and process.cmdline()[2] == "celery"

    def get_app_id(self, process: Process) -> Optional[str]:
        if not self.is_celery_process(process):
            return None

        appid = _get_cli_arg_by_name(process.cmdline(), "-A", check_for_short_prefix_arg=True) or _get_cli_arg_by_name(
            process.cmdline(), "--app", check_for_equals_arg=True
        )
        if appid is _NON_AVAILABLE_ARG:
            queue_name = _get_cli_arg_by_name(
                process.cmdline(), "-Q", check_for_short_prefix_arg=True
            ) or _get_cli_arg_by_name(process.cmdline(), "--queues", check_for_equals_arg=True)
            # TODO: One worker can handle multiple queues, it could be useful to encode that into the app id.
            if queue_name is not _NON_AVAILABLE_ARG:
                # The queue handler routing is defined in the directory where the worker is run
                return f"celery queue: {queue_name} ({process.cwd()})"
        if appid is _NON_AVAILABLE_ARG:
            _logger.warning(
                f"{self.__class__.__name__}: Couldn't find positional argument -A or --app for application indication",
                cmdline=process.cmdline(),
                no_extra_to_server=True,
            )
            return None

        return f"celery: {appid} ({_append_python_module_to_proc_wd(process, appid)})"


class _PySparkApplicationIdentifier(_ApplicationIdentifier):
    @staticmethod
    def _is_pyspark_process(process: Process) -> bool:
        # We're looking for pythonXX -m pyspark.daemon
        return _is_python_m_proc(process) and process.cmdline()[2] == "pyspark.daemon"

    def get_app_id(self, process: Process) -> Optional[str]:
        # TODO: detect application name from parent java native spark process.
        return "pyspark" if self._is_pyspark_process(process) else None


class _PythonModuleApplicationIdentifier(_ApplicationIdentifier):
    def get_app_id(self, process: Process) -> Optional[str]:
        if not _is_python_bin(_get_cli_arg_by_index(process.cmdline(), 0)):
            return None

        module_arg = _get_cli_arg_by_name(process.cmdline(), "-m")
        if module_arg is not _NON_AVAILABLE_ARG:
            return f"python: -m {module_arg}"

        arg_1 = _get_cli_arg_by_index(process.cmdline(), 1)
        if arg_1.endswith(".py"):
            return f"python: {arg_1} ({_append_python_module_to_proc_wd(process, arg_1)})"

        return None


class _NodeModuleApplicationIdentifier(_ApplicationIdentifier):
    def get_app_id(self, process: Process) -> Optional[str]:
        skip_next = False
        for arg in process.cmdline()[1:]:
            if skip_next:
                skip_next = False
                continue
            if arg.startswith("--require="):
                continue
            if arg in ["--require", "-r"]:
                skip_next = True
                continue
            if arg.endswith(".js"):
                return f"nodejs: {arg} ({_append_file_to_proc_wd(process, arg)})"
        return None


class _RubyModuleApplicationIdentifier(_ApplicationIdentifier):
    def get_app_id(self, process: Process) -> Optional[str]:
        skip_next = False
        for arg in process.cmdline():
            if skip_next:
                skip_next = False
                continue
            if arg.startswith("-r"):
                if len(arg) <= 2:
                    skip_next = True
                continue
            if arg.endswith(".rb"):
                return f"ruby: {arg} ({_append_file_to_proc_wd(process, arg)})"
        return None


class ApplicationIdentifiers:
    identifiers_map: Dict[str, List[_ApplicationIdentifier]]

    @classmethod
    def init(cls, enrichment_options: EnrichmentOptions) -> None:
        # Please note that the order matter, because the FIRST matching identifier will be used.
        # so when adding new identifiers pay attention to the order, unless aggregate_all is used.
        cls.identifiers_map = {
            "python": [
                _GunicornTitleApplicationIdentifier(),
                _GunicornApplicationIdentifier(),
                _UwsgiApplicationIdentifier(),
                _CeleryApplicationIdentifier(),
                _PySparkApplicationIdentifier(),
                _PythonModuleApplicationIdentifier(),
            ],
            "node": [_NodeModuleApplicationIdentifier()],
            "ruby": [_RubyModuleApplicationIdentifier()],
        }

        _ApplicationIdentifier.enrichment_options = enrichment_options

    @classmethod
    def init_java(cls, jattach_jcmd_runner: JattachJcmdRunner) -> None:
        if is_linux():
            cls.identifiers_map["java"] = [_JavaJarApplicationIdentifier(jattach_jcmd_runner)]
            cls.identifiers_map["java_spark"] = cls.identifiers_map["java"] + [_JavaSparkApplicationIdentifier()]


@functools.lru_cache(4096)  # NOTE: arbitrary cache size
def get_app_id(process: Process, runtime: str, aggregate_all: bool = False) -> Optional[str]:
    """
    Tries to identify the application running in a given process, application identification is fully heuristic,
    heuristics are being made on each application type available differ from each other and those their
    "heuristic level".
    """
    assert _ApplicationIdentifier.enrichment_options is not None, "not initialized?"
    if not _ApplicationIdentifier.enrichment_options.application_identifiers:
        return None

    appids = []
    for identifier in ApplicationIdentifiers.identifiers_map[runtime]:
        try:
            appid = identifier.get_app_id(process)
            if appid is not None:
                if not aggregate_all:
                    return appid
                appids.append(appid)

        except NoSuchProcess:
            break
        except Exception:
            _logger.exception(
                f"Application identifier {identifier} raised an exception while matching against process {process}"
            )
            continue

    if len(appids) != 0:
        return ", ".join(appids)
    return None


def get_python_app_id(process: Process) -> Optional[str]:
    return get_app_id(process, "python")


def get_java_app_id(process: Process, should_collect_spark_app_name: bool = False) -> Optional[str]:
    return get_app_id(process, "java_spark" if should_collect_spark_app_name else "java", aggregate_all=True)


def get_node_app_id(process: Process) -> Optional[str]:
    return get_app_id(process, "node")


def get_ruby_app_id(process: Process) -> Optional[str]:
    return get_app_id(process, "ruby")
