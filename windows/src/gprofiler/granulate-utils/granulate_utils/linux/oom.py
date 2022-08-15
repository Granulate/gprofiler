#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import re
from collections import namedtuple
from typing import Optional

from granulate_utils.linux import COMM_PATTERN

# see function in oom_kill.c:__oom_kill_process. example string:
# "Out of memory: Killed process 765074 (chrome) total-vm:38565352kB, anon-rss:209356kB, file-rss:1624kB, shmem-rss:0kB"
# recent kernels added more fields (e.g UID, pgtables) but we don't care about them, for now; and this regex still
# works.
KILLED_PROCESS = re.compile(
    r"(?:<\d>)?(?:\[(?P<timestamp>\d+\.\d+)\] )?(?:(?P<message>.*): )?Killed process (?P<pid>\d+) "
    rf"\((?P<comm>{COMM_PATTERN})\) total-vm:(?P<total_vm>\d+)kB, anon-rss:(?P<anon_rss>\d+)kB, "
    r"file-rss:(?P<file_rss>\d+)kB, shmem-rss:(?P<shmem_rss>\d+)kB"
)
KB = 1024

OomEntry = namedtuple("OomEntry", "timestamp message pid comm total_vm anon_rss file_rss shmem_rss")


def get_oom_entry(dmesg_line: str) -> Optional[OomEntry]:
    """
    Returns an OomEntry if this dmesg line contains an OOM message, otherwise None.
    """
    m = KILLED_PROCESS.search(dmesg_line)
    if m is not None:
        d = m.groupdict()
        ts = d["timestamp"]
        return OomEntry(
            float(ts) if ts is not None else None,
            d["message"],
            int(d["pid"]),
            d["comm"],
            int(d["total_vm"]) * KB,
            int(d["anon_rss"]) * KB,
            int(d["file_rss"]) * KB,
            int(d["shmem_rss"]) * KB,
        )
    else:
        return None
