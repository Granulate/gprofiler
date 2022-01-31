#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from pathlib import Path

from psutil import NoSuchProcess, Process


def process_comm(process: Process) -> str:
    try:
        status = Path(f"/proc/{process.pid}/status").read_text()
    except FileNotFoundError:
        raise NoSuchProcess(process.pid)

    name_line = status.splitlines()[0]
    assert name_line.startswith("Name:\t")
    return name_line.split("\t", 1)[1]
