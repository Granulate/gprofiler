#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import ctypes
import datetime
import glob
import logging
import os
import random
import re
import shutil
import signal
import socket
import string
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from subprocess import CompletedProcess, Popen, TimeoutExpired
from tempfile import TemporaryDirectory
from threading import Event
from typing import Any, Callable, Iterator, List, Optional, Union, cast

import psutil
from granulate_utils.exceptions import CouldNotAcquireMutex
from granulate_utils.linux.mutex import try_acquire_mutex
from granulate_utils.linux.ns import run_in_ns
from granulate_utils.linux.process import process_exe
from psutil import Process

from gprofiler.exceptions import (
    CalledProcessError,
    CalledProcessTimeoutError,
    ProcessStoppedException,
    ProgramMissingException,
    StopEventSetException,
)
from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)

GPROFILER_DIRECTORY_NAME = "gprofiler_tmp"
TEMPORARY_STORAGE_PATH = f"/tmp/{GPROFILER_DIRECTORY_NAME}"

gprofiler_mutex: Optional[socket.socket] = None


@lru_cache(maxsize=None)
def is_root() -> bool:
    return os.geteuid() == 0


libc: Optional[ctypes.CDLL] = None


def prctl(*argv: Any) -> int:
    global libc
    if libc is None:
        libc = ctypes.CDLL("libc.so.6", use_errno=True)
    return cast(int, libc.prctl(*argv))


PR_SET_PDEATHSIG = 1


def set_child_termination_on_parent_death() -> int:
    ret = prctl(PR_SET_PDEATHSIG, signal.SIGTERM)
    if ret != 0:
        errno = ctypes.get_errno()
        logger.warning(
            f"Failed to set parent-death signal on child process. errno: {errno}, strerror: {os.strerror(errno)}"
        )
    return ret


def wrap_callbacks(callbacks: List[Callable]) -> Callable:
    # Expects array of callback.
    # Returns one callback that call each one of them, and returns the retval of last callback
    def wrapper() -> Any:
        ret = None
        for cb in callbacks:
            ret = cb()

        return ret

    return wrapper


def wait_event(timeout: float, stop_event: Event, condition: Callable[[], bool], interval: float = 0.1) -> None:
    end_time = time.monotonic() + timeout
    while True:
        if condition():
            break

        if stop_event.wait(interval):
            raise StopEventSetException()

        if time.monotonic() > end_time:
            raise TimeoutError()


def get_iso8601_format_time_from_epoch_time(time: float) -> str:
    return get_iso8601_format_time(datetime.datetime.utcfromtimestamp(time))


def get_iso8601_format_time(time: datetime.datetime) -> str:
    return time.replace(microsecond=0).isoformat()


_INSTALLED_PROGRAMS_CACHE: List[str] = []


def assert_program_installed(program: str) -> None:
    if program in _INSTALLED_PROGRAMS_CACHE:
        return

    if shutil.which(program) is not None:
        _INSTALLED_PROGRAMS_CACHE.append(program)
    else:
        raise ProgramMissingException(program)


def grab_gprofiler_mutex() -> bool:
    """
    Implements a basic, system-wide mutex for gProfiler, to make sure we don't run 2 instances simultaneously.
    The mutex is implemented by a Unix domain socket bound to an address in the abstract namespace of the init
    network namespace. This provides automatic cleanup when the process goes down, and does not make any assumption
    on filesystem structure (as happens with file-based locks).
    In order to see who's holding the lock now, you can run "sudo netstat -xp | grep gprofiler".
    """
    GPROFILER_LOCK = "\x00gprofiler_lock"

    try:
        run_in_ns(["net"], lambda: try_acquire_mutex(GPROFILER_LOCK), passthrough_exception=True)
    except CouldNotAcquireMutex:
        print(
            "Could not acquire gProfiler's lock. Is it already running?"
            " Try 'sudo netstat -xp | grep gprofiler' to see which process holds the lock.",
            file=sys.stderr,
        )
        return False
    else:
        # success
        return True


def atomically_symlink(target: str, link_node: str) -> None:
    """
    Create a symlink file at 'link_node' pointing to 'target'.
    If a file already exists at 'link_node', it is replaced atomically.
    Would be obsoloted by https://bugs.python.org/issue36656, which covers this as well.
    """
    tmp_path = link_node + ".tmp"
    os.symlink(target, tmp_path)
    os.rename(tmp_path, link_node)


class TemporaryDirectoryWithMode(TemporaryDirectory):
    def __init__(self, *args: Any, mode: int = None, **kwargs: Any):
        super().__init__(*args, **kwargs)
        if mode is not None:
            os.chmod(self.name, mode)


def reset_umask() -> None:
    """
    Resets our umask back to a sane value.
    """
    os.umask(0o022)


def limit_frequency(
    limit: Optional[int], requested: int, msg_header: str, runtime_logger: logging.LoggerAdapter
) -> int:
    if limit is not None and requested > limit:
        runtime_logger.warning(
            f"{msg_header}: Requested frequency ({requested}) is higher than the limit {limit}, "
            f"limiting the frequency to the limit ({limit})"
        )
        return limit

    return requested


PERF_EVENT_MLOCK_KB = "/proc/sys/kernel/perf_event_mlock_kb"


def read_perf_event_mlock_kb() -> int:
    return int(Path(PERF_EVENT_MLOCK_KB).read_text())


def write_perf_event_mlock_kb(value: int) -> None:
    Path(PERF_EVENT_MLOCK_KB).write_text(str(value))


def is_pyinstaller() -> bool:
    """
    Are we running in PyInstaller?
    """
    # https://pyinstaller.readthedocs.io/en/stable/runtime-information.html#run-time-information
    return getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS")


def get_staticx_dir() -> Optional[str]:
    return os.getenv("STATICX_BUNDLE_DIR")


def add_permission_dir(path: str, permission_for_file: int, permission_for_dir: int) -> None:
    os.chmod(path, os.stat(path).st_mode | permission_for_dir)
    for subpath in os.listdir(path):
        absolute_subpath = os.path.join(path, subpath)
        if os.path.isdir(absolute_subpath):
            add_permission_dir(absolute_subpath, permission_for_file, permission_for_dir)
        else:
            os.chmod(absolute_subpath, os.stat(absolute_subpath).st_mode | permission_for_file)
