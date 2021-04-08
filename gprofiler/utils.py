#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import datetime
import logging
import os
import re
import subprocess
from functools import lru_cache
from subprocess import CompletedProcess, Popen, TimeoutExpired
from threading import Event
from typing import Iterator, Union, List, Optional
from pathlib import Path

import importlib_resources
import psutil
from psutil import Process

from gprofiler.exceptions import CalledProcessError, ProcessStoppedException

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


def run_process(
    cmd: Union[str, List[str]], stop_event: Event = None, suppress_log: bool = False, **kwargs
) -> CompletedProcess:
    cmd_text = " ".join(cmd) if isinstance(cmd, list) else cmd
    logger.debug(f'Running command: ({cmd_text})')
    if isinstance(cmd, str):
        cmd = [cmd]
    with Popen(
        cmd,
        stdout=kwargs.pop("stdout", subprocess.PIPE),
        stderr=kwargs.pop("stderr", subprocess.PIPE),
        preexec_fn=kwargs.pop("preexec_fn", os.setpgrp),
        **kwargs,
    ) as process:
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

    logger.debug(f"({cmd_text}) exit code: {result.returncode}")
    if not suppress_log:
        if result.stdout:
            logger.debug(f"({cmd_text}) stdout: {result.stdout}")
        if result.stderr:
            logger.debug(f"({cmd_text}) stderr: {result.stderr}")
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
    assert result.returncode in (0, 2)

    processes: List[Process] = []
    for line in result.stdout.splitlines():
        assert line.startswith(b"/proc/") and line.endswith(b"/maps")
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
