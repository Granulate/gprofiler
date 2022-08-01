#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from pathlib import Path

from granulate_utils.linux.process import is_process_running
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
