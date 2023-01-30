#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import contextlib
import re
from typing import Callable, Iterator, Match, Optional

from granulate_utils.linux.process import is_process_running, read_proc_file
from psutil import AccessDenied, NoSuchProcess, Process, process_iter

from gprofiler.platform import is_windows


def search_proc_maps(process: Process, pattern: str) -> Optional[Match[str]]:
    return re.search(pattern, read_proc_file(process, "maps").decode(), re.MULTILINE)


def process_comm(process: Process) -> str:
    if is_windows():
        return process.name()
    else:
        status = read_proc_file(process, "status").decode()
        name_line = status.splitlines()[0]
        assert name_line.startswith("Name:\t")
        return name_line.split("\t", 1)[1]


def search_for_process(filter: Callable[[Process], bool]) -> Iterator[Process]:
    for proc in process_iter():
        with contextlib.suppress(NoSuchProcess, AccessDenied):
            if is_process_running(proc) and filter(proc):
                yield proc
