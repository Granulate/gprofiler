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


def process_comm(process: Process) -> str:
    try:
        status = Path(f"/proc/{process.pid}/status").read_text()
    except FileNotFoundError:
        raise NoSuchProcess(process.pid)
    else:
        # ensures we read the right comm (i.e PID was not reused)
        ensure_running(process)

    name_line = status.splitlines()[0]
    assert name_line.startswith("Name:\t")
    return name_line.split("\t", 1)[1]


def is_musl(process: Process) -> bool:
    # TODO: make sure no glibc libc.so file exists (i.e, return True if musl, False if glibc, and raise
    # if not conclusive)
    return any("ld-musl" in m.path for m in process.memory_maps())
