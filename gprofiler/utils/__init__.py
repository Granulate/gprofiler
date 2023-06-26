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
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from subprocess import CompletedProcess, Popen, TimeoutExpired
from tempfile import TemporaryDirectory
from threading import Event
from typing import Any, Callable, Dict, Iterator, List, Optional, Tuple, Union, cast

import importlib_resources
import psutil
from granulate_utils.exceptions import CouldNotAcquireMutex
from granulate_utils.linux.mutex import try_acquire_mutex
from granulate_utils.linux.ns import run_in_ns
from granulate_utils.linux.process import is_kernel_thread, process_exe
from psutil import Process

from gprofiler.consts import CPU_PROFILING_MODE
from gprofiler.platform import is_linux, is_windows

if is_windows():
    import pythoncom
    import wmi

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
TEMPORARY_STORAGE_PATH = (
    f"/tmp/{GPROFILER_DIRECTORY_NAME}"
    if is_linux()
    else os.getenv("USERPROFILE", default=os.getcwd()) + f"\\AppData\\Local\\Temp\\{GPROFILER_DIRECTORY_NAME}"
)

gprofiler_mutex: Optional[socket.socket] = None


@lru_cache(maxsize=None)
def resource_path(relative_path: str = "") -> str:
    *relative_directory, basename = relative_path.split("/")
    package = ".".join(["gprofiler", "resources"] + relative_directory)
    try:
        with importlib_resources.path(package, basename) as path:
            return str(path)
    except ImportError as e:
        raise Exception(f"Resource {relative_path!r} not found!") from e


@lru_cache(maxsize=None)
def is_root() -> bool:
    if is_windows():
        return cast(int, ctypes.windll.shell32.IsUserAnAdmin()) == 1  # type: ignore
    else:
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


def start_process(
    cmd: Union[str, List[str]], via_staticx: bool = False, term_on_parent_death: bool = True, **kwargs: Any
) -> Popen:
    if isinstance(cmd, str):
        cmd = [cmd]

    logger.debug("Running command", command=cmd)

    env = kwargs.pop("env", None)
    staticx_dir = get_staticx_dir()
    # are we running under staticx?
    if staticx_dir is not None:
        # if so, if "via_staticx" was requested, then run the binary with the staticx ld.so
        # because it's supposed to be run with it.
        if via_staticx:
            # staticx_dir (from STATICX_BUNDLE_DIR) is where staticx has extracted all of the
            # libraries it had collected earlier.
            # see https://github.com/JonathonReinhart/staticx#run-time-information
            cmd = [f"{staticx_dir}/.staticx.interp", "--library-path", staticx_dir] + cmd
        else:
            # explicitly remove our directory from LD_LIBRARY_PATH
            env = env if env is not None else os.environ.copy()
            env.update({"LD_LIBRARY_PATH": ""})

    if is_windows():
        cur_preexec_fn = None  # preexec_fn is not supported on Windows platforms. subprocess.py reports this.
    else:
        cur_preexec_fn = kwargs.pop("preexec_fn", os.setpgrp)
        if term_on_parent_death:
            cur_preexec_fn = wrap_callbacks([set_child_termination_on_parent_death, cur_preexec_fn])

    popen = Popen(
        cmd,
        stdout=kwargs.pop("stdout", subprocess.PIPE),
        stderr=kwargs.pop("stderr", subprocess.PIPE),
        stdin=subprocess.PIPE,
        preexec_fn=cur_preexec_fn,
        env=env,
        **kwargs,
    )
    return popen


def wait_event(timeout: float, stop_event: Event, condition: Callable[[], bool], interval: float = 0.1) -> None:
    end_time = time.monotonic() + timeout
    while True:
        if condition():
            break

        if stop_event.wait(interval):
            raise StopEventSetException()

        if time.monotonic() > end_time:
            raise TimeoutError()


def poll_process(process: Popen, timeout: float, stop_event: Event) -> None:
    try:
        wait_event(timeout, stop_event, lambda: process.poll() is not None)
    except StopEventSetException:
        process.kill()
        raise


def remove_files_by_prefix(prefix: str) -> None:
    for f in glob.glob(f"{prefix}*"):
        os.unlink(f)


def wait_for_file_by_prefix(prefix: str, timeout: float, stop_event: Event) -> Path:
    glob_pattern = f"{prefix}*"
    wait_event(timeout, stop_event, lambda: len(glob.glob(glob_pattern)) > 0)

    output_files = glob.glob(glob_pattern)
    # All the snapshot samples should be in one file
    if len(output_files) != 1:
        # this can happen if:
        # * the profiler generating those files is erroneous
        # * the profiler received many signals (and it generated files based on signals)
        # * errors in gProfiler led to previous output fails remain not removed
        # in any case, we remove all old files, and assume the last one (after sorting by timestamp)
        # is the one we want.
        logger.warning(
            f"One output file expected, but found {len(output_files)}."
            f" Removing all and using the last one. {output_files}"
        )
        # timestamp format guarantees alphabetical order == chronological order.
        output_files.sort()
        for f in output_files[:-1]:
            os.unlink(f)
        output_files = output_files[-1:]

    return Path(output_files[0])


def reap_process(process: Popen) -> Tuple[int, bytes, bytes]:
    """
    Safely reap a process. This function expects the process to be exited or exiting.
    It uses communicate() instead of wait() to avoid the possible deadlock in wait()
    (see https://docs.python.org/3/library/subprocess.html#subprocess.Popen.wait, and see
    ticket https://github.com/Granulate/gprofiler/issues/744).
    """
    stdout, stderr = process.communicate()
    returncode = process.poll()
    assert returncode is not None  # only None if child has not terminated
    return returncode, stdout, stderr


def _kill_and_reap_process(process: Popen, kill_signal: signal.Signals) -> Tuple[int, bytes, bytes]:
    process.send_signal(kill_signal)
    logger.debug(
        f"({process.args!r}) was killed by us with signal {kill_signal} due to timeout or stop request, reaping it"
    )
    return reap_process(process)


def run_process(
    cmd: Union[str, List[str]],
    *,
    stop_event: Event = None,
    suppress_log: bool = False,
    via_staticx: bool = False,
    check: bool = True,
    timeout: int = None,
    kill_signal: signal.Signals = signal.SIGTERM if is_windows() else signal.SIGKILL,
    stdin: bytes = None,
    **kwargs: Any,
) -> "CompletedProcess[bytes]":
    stdout: bytes
    stderr: bytes

    reraise_exc: Optional[BaseException] = None
    with start_process(cmd, via_staticx, **kwargs) as process:
        assert isinstance(process.args, str) or (
            isinstance(process.args, list) and all(isinstance(s, str) for s in process.args)
        ), process.args  # mypy

        try:
            if stdin is not None:
                assert process.stdin is not None
                process.stdin.write(stdin)
            if stop_event is None:
                assert timeout is None, f"expected no timeout, got {timeout!r}"
                # wait for stderr & stdout to be closed
                stdout, stderr = process.communicate()
            else:
                end_time = (time.monotonic() + timeout) if timeout is not None else None
                while True:
                    try:
                        stdout, stderr = process.communicate(timeout=1)
                        break
                    except TimeoutExpired:
                        if stop_event.is_set():
                            raise ProcessStoppedException from None
                        if end_time is not None and time.monotonic() > end_time:
                            assert timeout is not None
                            raise
        except TimeoutExpired:
            returncode, stdout, stderr = _kill_and_reap_process(process, kill_signal)
            assert timeout is not None
            reraise_exc = CalledProcessTimeoutError(
                timeout, returncode, cmd, stdout.decode("latin-1"), stderr.decode("latin-1")
            )
        except BaseException as e:  # noqa
            returncode, stdout, stderr = _kill_and_reap_process(process, kill_signal)
            reraise_exc = e
        retcode = process.poll()
        assert retcode is not None  # only None if child has not terminated

    result: CompletedProcess[bytes] = CompletedProcess(process.args, retcode, stdout, stderr)

    # decoding stdout/stderr as latin-1 which should never raise UnicodeDecodeError.
    extra: Dict[str, Any] = {"exit_code": result.returncode}
    if not suppress_log:
        if result.stdout:
            extra["stdout"] = result.stdout.decode("latin-1")
        if result.stderr:
            extra["stderr"] = result.stderr.decode("latin-1")
    logger.debug("Command exited", command=process.args, **extra)
    if reraise_exc is not None:
        raise reraise_exc
    elif check and retcode != 0:
        raise CalledProcessError(
            retcode, process.args, output=stdout.decode("latin-1"), stderr=stderr.decode("latin-1")
        )
    return result


if is_windows():

    def pgrep_exe(match: str) -> List[Process]:
        """psutil doesn't return all running python processes on Windows"""
        pythoncom.CoInitialize()
        w = wmi.WMI()
        return [
            Process(pid=p.ProcessId)
            for p in w.Win32_Process()
            if match in p.Name.lower() and p.ProcessId != os.getpid()
        ]

else:

    def pgrep_exe(match: str) -> List[Process]:
        pattern = re.compile(match)
        procs = []
        for process in psutil.process_iter():
            try:
                if not is_kernel_thread(process) and pattern.match(process_exe(process)):
                    procs.append(process)
            except psutil.NoSuchProcess:  # process might have died meanwhile
                continue
        return procs


def pgrep_maps(match: str) -> List[Process]:
    # this is much faster than iterating over processes' maps with psutil.
    # We use flag -E in grep to support systems where grep is not PCRE
    result = run_process(
        f"grep -lE '{match}' /proc/*/maps",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        suppress_log=True,
        check=False,
    )
    # 0 - found
    # 1 - not found
    # 2 - error (which we might get for a missing /proc/pid/maps file of a process which just exited)
    # so this ensures grep wasn't killed by a signal
    assert result.returncode in (
        0,
        1,
        2,
    ), f"unexpected 'grep' exit code: {result.returncode}, stdout {result.stdout!r} stderr {result.stderr!r}"

    error_lines = []
    for line in result.stderr.splitlines():
        if not (
            line.startswith(b"grep: /proc/")
            and (line.endswith(b"/maps: No such file or directory") or line.endswith(b"/maps: No such process"))
        ):
            error_lines.append(line)
    if error_lines:
        logger.error(f"Unexpected 'grep' error output (first 10 lines): {error_lines[:10]}")

    processes: List[Process] = []
    for line in result.stdout.splitlines():
        assert line.startswith(b"/proc/") and line.endswith(b"/maps"), f"unexpected 'grep' line: {line!r}"
        pid = int(line[len(b"/proc/") : -len(b"/maps")])
        try:
            processes.append(Process(pid))
        except psutil.NoSuchProcess:
            continue  # process might have died meanwhile

    return processes


def get_iso8601_format_time_from_epoch_time(time: float) -> str:
    return get_iso8601_format_time(datetime.datetime.utcfromtimestamp(time))


def get_iso8601_format_time(time: datetime.datetime) -> str:
    return time.replace(microsecond=0).isoformat()


def remove_prefix(s: str, prefix: str) -> str:
    # like str.removeprefix of Python 3.9, but this also ensures the prefix exists.
    assert s.startswith(prefix), f"{s} doesn't start with {prefix}"
    return s[len(prefix) :]


def touch_path(path: str, mode: int) -> None:
    Path(path).touch()
    # chmod() afterwards (can't use 'mode' in touch(), because it's affected by umask)
    os.chmod(path, mode)


def remove_path(path: Union[str, Path], missing_ok: bool = False) -> None:
    # backporting missing_ok, available only from 3.8
    try:
        Path(path).unlink()
    except FileNotFoundError:
        if not missing_ok:
            raise


@contextmanager
def removed_path(path: str) -> Iterator[None]:
    try:
        yield
    finally:
        remove_path(path, missing_ok=True)


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
        run_in_ns(["net"], lambda: try_acquire_mutex(GPROFILER_LOCK))
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
    limit: Optional[int],
    requested: int,
    msg_header: str,
    runtime_logger: logging.LoggerAdapter,
    profiling_mode: str,
) -> int:
    if profiling_mode != CPU_PROFILING_MODE:
        return requested

    if limit is not None and requested > limit:
        runtime_logger.warning(
            f"{msg_header}: Requested frequency ({requested}) is higher than the limit {limit}, "
            f"limiting the frequency to the limit ({limit})"
        )
        return limit

    return requested


def random_prefix() -> str:
    return "".join(random.choice(string.ascii_letters) for _ in range(16))


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


def merge_dicts(source: Dict[str, Any], dest: Dict[str, Any]) -> Dict[str, Any]:
    for key, value in source.items():
        # in case value is a dict itself
        if isinstance(value, dict):
            node = dest.setdefault(key, {})
            merge_dicts(value, node)
        else:
            dest[key] = value
    return dest
