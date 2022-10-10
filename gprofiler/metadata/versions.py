#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from subprocess import CompletedProcess
from threading import Event

from granulate_utils.linux.ns import get_process_nspid, run_in_ns
from psutil import Process

from gprofiler.utils import run_process


def get_exe_version(
    process: Process,
    stop_event: Event,
    get_version_timeout: int,
    version_arg: str = "--version",
    try_stderr: bool = False,
) -> str:
    """
    Runs {process.exe()} --version in the appropriate namespace
    """
    exe_path = f"/proc/{get_process_nspid(process.pid)}/exe"

    def _run_get_version() -> "CompletedProcess[bytes]":
        return run_process([exe_path, version_arg], stop_event=stop_event, timeout=get_version_timeout)

    cp = run_in_ns(["pid", "mnt"], _run_get_version, process.pid)
    stdout = cp.stdout.decode().strip()
    # return stderr if stdout is empty, some apps print their version to stderr.
    if try_stderr and not stdout:
        return cp.stderr.decode().strip()

    return stdout
