#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import datetime
import logging
import os
import re
import time
import shutil
import subprocess
import platform
import ctypes
import signal
from functools import lru_cache
from subprocess import CompletedProcess, Popen, TimeoutExpired
from threading import Event, Thread
from typing import Iterator, Union, List, Optional, Tuple
from pathlib import Path

import importlib_resources
import prctl
import psutil
import distro  # type: ignore
from psutil import Process

from gprofiler.exceptions import (
    CalledProcessError,
    ProcessStoppedException,
    ProgramMissingException,
    StopEventSetException,
)

logger = logging.getLogger(__name__)

TEMPORARY_STORAGE_PATH = "/tmp/gprofiler"


def resource_path(relative_path: str = "") -> str:
    *relative_directory, basename = relative_path.split("/")
    package = ".".join(["gprofiler", "resources"] + relative_directory)
    with importlib_resources.path(package, basename) as path:
        return str(path)


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


def _preexec_fn():
    os.setpgrp()
    prctl.set_pdeathsig(signal.SIGTERM)


def start_process(cmd: Union[str, List[str]], **kwargs) -> Popen:
    cmd_text = " ".join(cmd) if isinstance(cmd, list) else cmd
    logger.debug(f"Running command: ({cmd_text})")
    if isinstance(cmd, str):
        cmd = [cmd]
    popen = Popen(
        cmd,
        stdout=kwargs.pop("stdout", subprocess.PIPE),
        stderr=kwargs.pop("stderr", subprocess.PIPE),
        preexec_fn=kwargs.pop("preexec_fn", _preexec_fn),
        **kwargs,
    )
    return popen


def poll_process(process, timeout, stop_event):
    timefn = time.monotonic
    endtime = timefn() + timeout
    while True:
        try:
            process.wait(0.1)
            break
        except TimeoutExpired:
            if stop_event.is_set():
                process.kill()
                raise StopEventSetException()
            if timefn() > endtime:
                raise TimeoutError()


def run_process(
    cmd: Union[str, List[str]], stop_event: Event = None, suppress_log: bool = False, **kwargs
) -> CompletedProcess:
    with start_process(cmd, **kwargs) as process:
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
    result = subprocess.run(
        f"grep -lP '{match}' /proc/*/maps", stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, shell=True
    )
    # might get 2 (which 'grep' exits with, if some files were unavailable, because processes have exited)
    assert result.returncode in (0, 2), f"unexpected 'grep' exit code: {result.returncode}"

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
    ldd_version = subprocess.run(["ldd", "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT).stdout
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


def log_system_info():
    uname = platform.uname()
    logger.info(f"Kernel uname release: {uname.release}")
    logger.info(f"Kernel uname version: {uname.version}")
    logger.info(f"Total CPUs: {os.cpu_count()}")
    logger.info(f"Total RAM: {psutil.virtual_memory().total / (1 << 30):.2f} GB")

    # we can't setns(CLONE_NEWNS) in a multithreaded program (unless we unshare(CLONE_NEWNS) before)
    # so, we start a new thread, unshare() & setns() it, get our needed information and then stop this thread
    # (so we don't keep unshared threads running around)
    results = []

    def get_distro_and_libc():
        # move to host mount NS for distro & ldd.
        if not is_same_ns(1, "mnt"):
            libc = ctypes.CDLL("libc.so.6")

            CLONE_NEWNS = 0x00020000
            if libc.unshare(CLONE_NEWNS) != 0 or libc.setns(os.open("/proc/1/ns/mnt", os.O_RDONLY), CLONE_NEWNS) != 0:
                raise ValueError("Failed to unshare() and setns()")

        # now, distro will read the files on host.
        results.append(distro.linux_distribution())
        results.append(get_libc_version())

    t = Thread(target=get_distro_and_libc)
    t.start()
    t.join()
    assert len(results) == 2, f"only {len(results)} results, expected 2"

    logger.info(f"Linux distribution: {results[0]}")
    logger.info(f"libc version: {results[1]}")
