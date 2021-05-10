#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import ctypes
import datetime
import errno
import fcntl
import logging
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path
from subprocess import CompletedProcess, Popen, TimeoutExpired
from tempfile import TemporaryDirectory
from threading import Event, Thread
from typing import Callable, Iterator, List, Optional, Tuple, Union

import distro  # type: ignore
import importlib_resources
import psutil
from psutil import Process

from gprofiler.exceptions import (
    CalledProcessError,
    ProcessStoppedException,
    ProgramMissingException,
    StopEventSetException,
)

logger = logging.getLogger(__name__)

TEMPORARY_STORAGE_PATH = "/tmp/gprofiler_tmp"

gprofiler_mutex: Optional[socket.socket]


def resource_path(relative_path: str = "") -> str:
    *relative_directory, basename = relative_path.split("/")
    package = ".".join(["gprofiler", "resources"] + relative_directory)
    try:
        with importlib_resources.path(package, basename) as path:
            return str(path)
    except ImportError as e:
        raise Exception(f'Resource {relative_path!r} not found!') from e


@lru_cache(maxsize=None)
def is_root() -> bool:
    return os.geteuid() == 0


def get_process_container_id(pid: int) -> Optional[str]:
    with open(f"/proc/{pid}/cgroup") as f:
        for line in f:
            line = line.strip()
            if any(s in line for s in (":/docker/", ":/ecs/", ":/kubepods", ":/lxc/")):
                return line.split("/")[-1]
        return None


@lru_cache(maxsize=None)
def get_self_container_id() -> Optional[str]:
    return get_process_container_id(os.getpid())


def get_process_nspid(pid: int) -> int:
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            fields = line.split()
            if fields[0] == "NSpid:":
                return int(fields[-1])

    raise Exception(f"Couldn't find NSpid for pid {pid}")


def start_process(cmd: Union[str, List[str]], via_staticx: bool, **kwargs) -> Popen:
    cmd_text = " ".join(cmd) if isinstance(cmd, list) else cmd
    logger.debug(f"Running command: ({cmd_text})")
    if isinstance(cmd, str):
        cmd = [cmd]

    staticx_dir = os.getenv("STATICX_BUNDLE_DIR")
    # are we running under staticx?
    if staticx_dir is not None:
        # if so, if "via_staticx" was requested, then run the binary with the staticx ld.so
        # because it's supposed to be run with it.
        if via_staticx:
            # STATICX_BUNDLE_DIR is where staticx has extracted all of the libraries it had collected
            # earlier.
            # see https://github.com/JonathonReinhart/staticx#run-time-information
            cmd = [f"{staticx_dir}/.staticx.interp", "--library-path", staticx_dir] + cmd
            env = kwargs.pop("env", None)
        else:
            # explicitly remove our directory from LD_LIBRARY_PATH
            env = os.environ.copy()
            env.update(kwargs.pop("env", {}))
            env.update({"LD_LIBRARY_PATH": ""})
    else:
        env = None

    popen = Popen(
        cmd,
        stdout=kwargs.pop("stdout", subprocess.PIPE),
        stderr=kwargs.pop("stderr", subprocess.PIPE),
        preexec_fn=kwargs.pop("preexec_fn", os.setpgrp),
        env=env,
        **kwargs,
    )
    return popen


def wait_event(timeout: float, stop_event: Event, condition: Callable[[], bool]) -> None:
    end_time = time.monotonic() + timeout
    while True:
        if condition():
            break

        if stop_event.wait(0.1):
            raise StopEventSetException()

        if time.monotonic() > end_time:
            raise TimeoutError()


def poll_process(process, timeout: float, stop_event: Event):
    try:
        wait_event(timeout, stop_event, lambda: process.poll() is not None)
    except StopEventSetException:
        process.kill()
        raise


def run_process(
    cmd: Union[str, List[str]],
    stop_event: Event = None,
    suppress_log: bool = False,
    via_staticx: bool = False,
    **kwargs,
) -> CompletedProcess:
    with start_process(cmd, via_staticx, **kwargs) as process:
        try:
            if stop_event is None:
                stdout, stderr = process.communicate()
            else:
                while True:
                    try:
                        stdout, stderr = process.communicate(timeout=1)
                        break
                    except TimeoutExpired:
                        if stop_event.is_set():
                            raise ProcessStoppedException from None
        except:  # noqa
            process.kill()
            process.wait()
            raise
        retcode = process.poll()
        assert retcode is not None  # only None if child has not terminated
    result: CompletedProcess = CompletedProcess(process.args, retcode, stdout, stderr)

    logger.debug(f"({process.args!r}) exit code: {result.returncode}")
    if not suppress_log:
        if result.stdout:
            logger.debug(f"({process.args!r}) stdout: {result.stdout}")
        if result.stderr:
            logger.debug(f"({process.args!r}) stderr: {result.stderr}")
    if retcode:
        raise CalledProcessError(retcode, process.args, output=stdout, stderr=stderr)
    return result


def pgrep_exe(match: str) -> Iterator[Process]:
    pattern = re.compile(match)
    return (process for process in psutil.process_iter() if pattern.match(process.exe()))


def pgrep_maps(match: str) -> List[Process]:
    # this is much faster than iterating over processes' maps with psutil.
    result = run_process(
        f"grep -lP '{match}' /proc/*/maps",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        shell=True,
        suppress_log=True,
    )
    # might get 2 (which 'grep' exits with, if some files were unavailable, because processes have exited)
    assert result.returncode in (
        0,
        2,
    ), f"unexpected 'grep' exit code: {result.returncode}, stdout {result.stdout!r} stderr {result.stderr!r}"

    processes: List[Process] = []
    for line in result.stdout.splitlines():
        assert line.startswith(b"/proc/") and line.endswith(b"/maps"), f"unexpected 'grep' line: {line!r}"
        pid = int(line[len(b"/proc/") : -len(b"/maps")])
        processes.append(Process(pid))

    return processes


def get_iso8061_format_time(time: datetime.datetime) -> str:
    return time.replace(microsecond=0).isoformat()


def resolve_proc_root_links(proc_root: str, ns_path: str) -> str:
    """
    Resolves "ns_path" which (possibly) resides in another mount namespace.

    If ns_path contains absolute symlinks, it can't be accessed merely by /proc/pid/root/ns_path,
    because the resolved absolute symlinks will "escape" the /proc/pid/root base.

    To work around that, we resolve the path component by component; if any component "escapes", we
    add the /proc/pid/root prefix once again.
    """
    parts = Path(ns_path).parts
    assert parts[0] == "/", f"expected {ns_path!r} to be absolute"

    path = proc_root
    for part in parts[1:]:  # skip the /
        next_path = os.path.join(path, part)
        if os.path.islink(next_path):
            link = os.readlink(next_path)
            if os.path.isabs(link):
                # absolute - prefix with proc_root
                next_path = proc_root + link
            else:
                # relative: just join
                next_path = os.path.join(path, link)
        path = next_path

    return path


def remove_prefix(s: str, prefix: str) -> str:
    # like str.removeprefix of Python 3.9, but this also ensures the prefix exists.
    assert s.startswith(prefix)
    return s[len(prefix) :]


def touch_path(path: str, mode: int) -> None:
    Path(path).touch()
    # chmod() afterwards (can't use 'mode' in touch(), because it's affected by umask)
    os.chmod(path, mode)


def is_same_ns(pid: int, nstype: str) -> bool:
    return os.stat(f"/proc/self/ns/{nstype}").st_ino == os.stat(f"/proc/{pid}/ns/{nstype}").st_ino


_INSTALLED_PROGRAMS_CACHE: List[str] = []


def assert_program_installed(program: str):
    if program in _INSTALLED_PROGRAMS_CACHE:
        return

    if shutil.which(program) is not None:
        _INSTALLED_PROGRAMS_CACHE.append(program)
    else:
        raise ProgramMissingException(program)


def get_libc_version() -> Tuple[str, bytes]:
    # platform.libc_ver fails for musl, sadly (produces empty results).
    # so we'll run "ldd --version" and extract the version string from it.
    # not passing "encoding"/"text" - this runs in a different mount namespace, and Python fails to
    # load the files it needs for those encodings (getting LookupError: unknown encoding: ascii)
    ldd_version = run_process(
        ["ldd", "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, suppress_log=True
    ).stdout
    # catches GLIBC & EGLIBC
    m = re.search(br"GLIBC (.*?)\)", ldd_version)
    if m is not None:
        return ("glibc", m.group(1))
    # catches GNU libc
    m = re.search(br"\(GNU libc\) (.*?)\n", ldd_version)
    if m is not None:
        return ("glibc", m.group(1))
    # musl
    m = re.search(br"musl libc.*?\nVersion (.*?)\n", ldd_version, re.M)
    if m is not None:
        return ("musl", m.group(1))

    return ("unknown", ldd_version)


def get_run_mode() -> str:
    if os.getenv("GPROFILER_IN_K8S") is not None:  # set in k8s/gprofiler.yaml
        return "k8s"
    elif os.getenv("GPROFILER_IN_CONTAINER") is not None:  # set by our Dockerfile
        return "container"
    elif os.getenv("STATICX_BUNDLE_DIR") is not None:  # set by staticx
        return "standalone_executable"
    else:
        return "local_python"


def run_in_ns(nstypes: List[str], callback: Callable[[], None], target_pid: int = 1) -> None:
    """
    Runs a callback in a new thread, switching to a set of the namespaces of a target process before
    doing so.

    Needed initially for swithcing mount namespaces, because we can't setns(CLONE_NEWNS) in a multithreaded
    program (unless we unshare(CLONE_NEWNS) before). so, we start a new thread, unshare() & setns() it,
    run our callback and then stop the thread (so we don't keep unshared threads running around).
    For other namespace types, we use this function to execute callbacks without changing the namespaces
    for the core threads.

    By default, run stuff in init NS. You can pass 'target_pid' to run in the namespace of that process.
    """

    # make sure "mnt" is last, once we change it our /proc is gone
    nstypes = sorted(nstypes, key=lambda ns: 1 if ns == "mnt" else 0)

    def _switch_and_run():
        libc = ctypes.CDLL("libc.so.6")
        for nstype in nstypes:
            if not is_same_ns(target_pid, nstype):
                flag = {
                    "mnt": 0x00020000,  # CLONE_NEWNS
                    "net": 0x40000000,  # CLONE_NEWNET
                    "pid": 0x20000000,  # CLONE_NEWPID
                }[nstype]
                if (
                    libc.unshare(flag) != 0
                    or libc.setns(os.open(f"/proc/{target_pid}/ns/{nstype}", os.O_RDONLY), flag) != 0
                ):
                    raise ValueError(f"Failed to unshare({nstype}) and setns({nstype})")

        callback()

    t = Thread(target=_switch_and_run)
    t.start()
    t.join()


def log_system_info():
    uname = platform.uname()
    logger.info(f"gProfiler Python version: {sys.version}")
    logger.info(f"gProfiler run mode: {get_run_mode()}")
    logger.info(f"Kernel uname release: {uname.release}")
    logger.info(f"Kernel uname version: {uname.version}")
    logger.info(f"Total CPUs: {os.cpu_count()}")
    logger.info(f"Total RAM: {psutil.virtual_memory().total / (1 << 30):.2f} GB")

    results = []

    # move to host mount NS for distro & ldd.
    # now, distro will read the files on host.
    def get_distro_and_libc():
        results.append(distro.linux_distribution())
        results.append(get_libc_version())

    run_in_ns(["mnt"], get_distro_and_libc)
    assert len(results) == 2, f"only {len(results)} results, expected 2"

    logger.info(f"Linux distribution: {results[0]}")
    logger.info(f"libc version: {results[1]}")


def grab_gprofiler_mutex() -> bool:
    """
    Implements a basic, system-wide mutex for gProfiler, to make sure we don't run 2 instances simultaneously.
    The mutex is implemented by a Unix domain socket bound to an address in the abstract namespace of the init
    network namespace. This provides automatic cleanup when the process goes down, and does not make any assumption
    on filesystem structure (as happens with file-based locks).
    In order to see who's holding the lock now, you can run "sudo netstat -xp | grep gprofiler".
    """
    GPROFILER_LOCK = "\x00gprofiler_lock"

    global gprofiler_mutex
    gprofiler_mutex = None

    def _take_lock():
        global gprofiler_mutex

        s = socket.socket(socket.AF_UNIX)
        try:
            s.bind(GPROFILER_LOCK)
        except OSError as e:
            if e.errno != errno.EADDRINUSE:
                raise
        else:
            # don't let child programs we execute inherit it.
            fcntl.fcntl(s, fcntl.F_SETFD, fcntl.fcntl(s, fcntl.F_GETFD) | fcntl.FD_CLOEXEC)

            # hold the reference so lock remains taken
            gprofiler_mutex = s

    run_in_ns(["net"], _take_lock)

    return gprofiler_mutex is not None


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
    def __init__(self, *args, mode: int = None, **kwargs):
        super().__init__(*args, **kwargs)
        if mode is not None:
            os.chmod(self.name, mode)


def reset_umask() -> None:
    """
    Resets our umask back to a sane value.
    """
    os.umask(0o022)
