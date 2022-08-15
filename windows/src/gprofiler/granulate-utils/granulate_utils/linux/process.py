#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import psutil


def process_exe(process: psutil.Process) -> str:
    """
    psutil.Process(pid).exe() returns "" for zombie processes, incorrectly. It should raise ZombieProcess, and return ""
    only for kernel threads.

    See https://github.com/giampaolo/psutil/pull/2062
    """
    exe = process.exe()
    if exe == "" and is_process_zombie(process):
        raise psutil.ZombieProcess(process.pid)
    return exe


def is_process_running(process: psutil.Process, allow_zombie: bool = False) -> bool:
    """
    psutil.Process(pid).is_running() considers zombie processes as running. This utility can be used to check if a
    process is actually running and not in a zombie state
    """
    return process.is_running() and (allow_zombie or not is_process_zombie(process))


def is_process_zombie(process: psutil.Process) -> bool:
    return process.status() == "zombie"


def is_musl(process: psutil.Process) -> bool:
    # TODO: make sure no glibc libc.so file exists (i.e, return True if musl, False if glibc, and raise
    # if not conclusive)
    return any("ld-musl" in m.path for m in process.memory_maps())
