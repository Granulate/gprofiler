#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import os
import re
from functools import lru_cache
from pathlib import Path

from granulate_utils.linux.process import is_process_running, process_exe
from psutil import NoSuchProcess, Process


def ensure_running(process: Process) -> None:
    if not is_process_running(process, allow_zombie=True):
        raise NoSuchProcess(process.pid)


def read_proc_file(process: Process, file: str) -> str:
    try:
        data = Path(f"/proc/{process.pid}/{file}").read_text()
    except FileNotFoundError as e:
        raise NoSuchProcess(process.pid) from e
    else:
        # ensures we read the right file (i.e PID was not reused)
        ensure_running(process)
    return data


def process_comm(process: Process) -> str:
    status = read_proc_file(process, "status")
    name_line = status.splitlines()[0]
    assert name_line.startswith("Name:\t")
    return name_line.split("\t", 1)[1]


@lru_cache(maxsize=512)
def is_process_basename_matching(process: Process, basename_pattern: str) -> bool:
    if re.match(basename_pattern, os.path.basename(process_exe(process))):
        return True

    # process was executed AS basename (but has different exe name)
    cmd = process.cmdline()
    if len(cmd) > 0 and re.match(basename_pattern, cmd[0]):
        return True

    return False
