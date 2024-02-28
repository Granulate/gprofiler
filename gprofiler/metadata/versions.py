#
# Copyright (C) 2023 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
from subprocess import CompletedProcess
from threading import Event

from granulate_utils.linux.ns import get_process_nspid, run_in_ns
from psutil import NoSuchProcess, Process

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

    try:
        cp = run_in_ns(["pid", "mnt"], _run_get_version, process.pid)
    except FileNotFoundError as e:
        if not process.is_running():
            raise NoSuchProcess(process.pid)
        else:
            raise e

    stdout = cp.stdout.decode().strip()
    # return stderr if stdout is empty, some apps print their version to stderr.
    if try_stderr and not stdout:
        return cp.stderr.decode().strip()

    return stdout
