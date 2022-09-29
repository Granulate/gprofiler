#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import os
import re
from functools import lru_cache
from typing import Match, Optional

from granulate_utils.linux.process import process_exe, read_proc_file
from psutil import Process

from gprofiler.platform import is_windows


def search_proc_maps(process: Process, pattern: str) -> Optional[Match[str]]:
    return re.search(pattern, read_proc_file(process, "maps").decode(), re.MULTILINE)


def process_comm(process: Process) -> str:
    if is_windows():
        # TODO: Check if process is running
        return process.name()
    else:
        status = read_proc_file(process, "status").decode()
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
