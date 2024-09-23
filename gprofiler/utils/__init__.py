#
# Copyright (C) 2022 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import os
import re
from functools import lru_cache
from typing import List

import importlib_resources
import psutil
from granulate_utils.linux.process import is_kernel_thread, process_exe
from psutil import Process

from granulate_utils.gprofiler.platform import is_windows
import granulate_utils.gprofiler.utils as _utils
from granulate_utils.gprofiler.utils import *

if is_windows():
    import pythoncom
    import wmi

from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)

@lru_cache(maxsize=None)
def resource_path(relative_path: str = "") -> str:
    *relative_directory, basename = relative_path.split("/")
    package = ".".join(["gprofiler", "resources"] + relative_directory)
    try:
        with importlib_resources.path(package, basename) as path:
            return str(path)
    except ImportError as e:
        raise Exception(f"Resource {relative_path!r} not found!") from e

if is_windows():

    def pgrep_exe(match: str) -> List[Process]:
        """psutil doesn't return all running python processes on Windows"""
        pythoncom.CoInitialize()
        w = wmi.WMI()
        return [
            Process(pid=p.ProcessId)
            for p in w.Win32_Process()
            if match in p.Name.lower() and p.ProcessId != os.getpid()
        ]

else:

    def pgrep_exe(match: str) -> List[Process]:
        pattern = re.compile(match)
        procs = []
        for process in psutil.process_iter():
            try:
                if not is_kernel_thread(process) and pattern.match(process_exe(process)):
                    procs.append(process)
            except psutil.NoSuchProcess:  # process might have died meanwhile
                continue
        return procs


def set_child_termination_on_parent_death() -> int:
    return _utils.set_child_termination_on_parent_death(logger)


def start_process(
    cmd: Union[str, List[str]],
    via_staticx: bool = False,
    term_on_parent_death: bool = True,
    **kwargs: Any,
) -> Popen:
    return _utils.start_process(cmd, via_staticx, term_on_parent_death, **kwargs)


def wait_for_file_by_prefix(
    prefix: str,
    timeout: float,
    stop_event: Event,
) -> Path:
    return _utils.wait_for_file_by_prefix(prefix, timeout, stop_event, logger)


def run_process(
    cmd: Union[str, List[str]],
    *,
    stop_event: Event = None,
    suppress_log: bool = False,
    via_staticx: bool = False,
    check: bool = True,
    timeout: int = None,
    kill_signal: signal.Signals = signal.SIGTERM if is_windows() else signal.SIGKILL,
    stdin: bytes = None,
    **kwargs: Any,
) -> "CompletedProcess[bytes]":
    return _utils.run_process(
        cmd,
        logger,
        stop_event=stop_event,
        suppress_log=suppress_log,
        via_staticx=via_staticx,
        check=check,
        timeout=timeout,
        kill_signal=kill_signal,
        stdin=stdin,
        **kwargs,
    )


def pgrep_maps(match: str) -> List[Process]:
    return _utils.pgrep_maps(match, logger)
