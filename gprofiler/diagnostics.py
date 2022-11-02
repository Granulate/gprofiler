#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import subprocess
import time
from io import StringIO
from typing import Optional

from granulate_utils.containers.client import ContainersClient
from granulate_utils.exceptions import MissingExePath, NoContainerRuntimesError
from granulate_utils.linux.process import is_kernel_thread, process_exe
from psutil import NoSuchProcess, Process, process_iter

from gprofiler.log import get_logger_adapter
from gprofiler.utils.process import process_comm

logger = get_logger_adapter(__name__)

# Log extra verbose information, making the debugging of gProfiler easier.
diagnostics_mode: Optional[bool] = None


def set_diagnostics(diagnostics: bool) -> None:
    global diagnostics_mode
    assert diagnostics_mode is None
    diagnostics_mode = diagnostics


def is_diagnostics() -> bool:
    assert diagnostics_mode is not None
    return diagnostics_mode


def _log_containers() -> None:
    try:
        cc = ContainersClient()
    except NoContainerRuntimesError:
        logger.debug("No container runtimes found")
    else:
        logger.debug("Running containers", containers=cc.list_containers())


def _process_info(p: Process) -> str:
    if is_kernel_thread(p):
        exe = "(none)"
    else:
        try:
            exe = process_exe(p)
        except MissingExePath:
            exe = "(missing)"

    return (
        f"pid={p.pid} ppid={p.ppid()} comm={process_comm(p)!r} exe={exe!r} uids={p.uids()} gids={p.gids()}"
        f" num_threads={p.num_threads()} cpu_percent={p.cpu_percent()!r} memory_info={p.memory_info()}"
        f" cmdline={' '.join(p.cmdline())!r}\n"
    )


def _log_processes() -> None:
    ps = list(process_iter())
    for p in ps:
        try:
            # first cpu_percent() calls lets psutil store the counter, next call will give us the actual value.
            p.cpu_percent()
        except NoSuchProcess:
            pass

    time.sleep(0.1)  # wait a bit, for cpu_percent

    buf = StringIO()
    for p in ps:
        try:
            buf.write(_process_info(p))
        except NoSuchProcess:
            continue
        except Exception as e:
            buf.write(f"pid={p.pid} exception={str(e)}")
    logger.debug("Running processes", processes=buf.getvalue())


def _log_dmesg() -> None:
    try:
        output = subprocess.run(["dmesg", "-T"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout.decode()
    except Exception as e:
        output = str(e)

    logger.debug("Kernel log", dmesg=output)


def log_diagnostics() -> None:
    if not is_diagnostics():
        return

    _log_containers()
    _log_processes()
    _log_dmesg()
