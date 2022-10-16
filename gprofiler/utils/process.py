#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import re
from typing import Match, Optional

from granulate_utils.linux.process import read_proc_file
from psutil import Process


def search_proc_maps(process: Process, pattern: str) -> Optional[Match[str]]:
    return re.search(pattern, read_proc_file(process, "maps").decode(), re.MULTILINE)


def process_comm(process: Process) -> str:
    status = read_proc_file(process, "status").decode()
    name_line = status.splitlines()[0]
    assert name_line.startswith("Name:\t")
    return name_line.split("\t", 1)[1]
