#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import ctypes
import enum
import os
import re
from pathlib import Path
from threading import Thread
from typing import Callable, List, Optional, TypeVar, Union

from psutil import NoSuchProcess, Process, process_iter

from granulate_utils.exceptions import UnsupportedNamespaceError
from granulate_utils.linux.containers import get_process_container_id

T = TypeVar("T")

HOST_ROOT_PREFIX = "/proc/1/root"


class _Sentinel:
    pass


_SENTINEL = _Sentinel()


class NsType(enum.IntFlag):
    mnt = 0x00020000  # CLONE_NEWNS
    net = 0x40000000  # CLONE_NEWNET
    pid = 0x20000000  # CLONE_NEWPID
    uts = 0x04000000  # CLONE_NEWUTS
    cgroup = 0x02000000  # CLONE_NEWCGROUP
    ipc = 0x08000000  # CLONE_NEWIPC
    user = 0x10000000  # CLONE_NEWUSER


libc: Optional[ctypes.CDLL] = None


def resolve_host_root_links(ns_path: str) -> str:
    return resolve_proc_root_links(HOST_ROOT_PREFIX, ns_path)


def resolve_proc_root_links(proc_root: str, ns_path: str) -> str:
    """
    Resolves "ns_path" which (possibly) resides in another mount namespace.

    If ns_path contains absolute symlinks, it can't be accessed merely by /proc/pid/root/ns_path,
    because the resolved absolute symlinks will "escape" the /proc/pid/root base.

    To work around that, we resolve the path component by component; if any component "escapes", we
    add the /proc/pid/root prefix once again.
    """
    assert ns_path[0] == "/", f"expected {ns_path!r} to be absolute"
    parts = Path(ns_path).parts

    path = proc_root
    seen = set()
    for part in parts[1:]:  # skip the / (or multiple /// as .parts gives them)
        next_path = os.path.join(path, part)
        while os.path.islink(next_path):
            if next_path in seen:
                raise RuntimeError("Symlink loop from %r" % os.path.join(path, part))
            seen.add(next_path)
            link = os.readlink(next_path)
            if os.path.isabs(link):
                # absolute - prefix with proc_root
                next_path = proc_root + link
            else:
                # relative: just join
                next_path = os.path.join(os.path.dirname(next_path), link)
        path = next_path

    return path


def get_process_nspid(process: Union[Process, int]) -> int:
    """
    :raises NoSuchProcess: If the process doesn't or no longer exists
    """
    if isinstance(process, int):
        process = Process(process)

    nspid = _get_process_nspid_by_status_file(process)
    if nspid is not None:
        return nspid

    if is_same_ns(process, NsType.pid.name):
        # If we're in the same PID namespace, then the outer PID is also the inner pid (NSpid)
        return process.pid

    return _get_process_nspid_by_sched_files(process)


def _get_process_nspid_by_status_file(process: Process) -> Optional[int]:
    try:
        with open(f"/proc/{process.pid}/status") as f:
            # If the process isn't running, then we opened the wrong `status` file
            if not process.is_running():
                raise NoSuchProcess(process.pid)

            for line in f:
                fields = line.split()
                if fields[0] == "NSpid:":
                    return int(fields[-1])  # The last pid in the list is the innermost pid, according to `man 5 proc`

        return None
    except (FileNotFoundError, ProcessLookupError) as e:
        raise NoSuchProcess(process.pid) from e


def _get_process_nspid_by_sched_files(process: Process) -> int:
    # Old kernel (pre 4.1) doesn't have an NSpid field in their /proc/pid/status file
    # Instead, we can look through all /proc/*/sched files from inside the process' pid namespace, and due to a bug
    # (fixed in 4.14) the outer PID is exposed, so we can find the target process by comparing the outer PID

    def _find_inner_pid() -> Optional[int]:
        pattern = re.compile(r"\((\d+), #threads: ")  # Match example: "java (12329, #threads: 11)"

        procfs = Path("/proc")
        for procfs_child in procfs.iterdir():
            is_process_dir = procfs_child.is_dir() and procfs_child.name.isdigit()
            if not is_process_dir:
                continue

            try:
                sched_file_path = procfs_child / "sched"
                with sched_file_path.open("r") as sched_file:
                    sched_header_line = sched_file.readline()  # The first line contains the outer PID
            except (FileNotFoundError, ProcessLookupError):
                # That's OK, processes might disappear before we get the chance to handle them
                continue

            match = pattern.search(sched_header_line)
            if match is not None:
                outer_pid = int(match.group(1))
                if outer_pid == process.pid:
                    return int(procfs_child.name)

        return None

    # We're searching `/proc`, so we only need to set our mount namespace
    inner_pid = run_in_ns(["mnt"], _find_inner_pid, process.pid)
    if inner_pid is not None:
        if not process.is_running():  # Make sure the pid wasn't reused for another process
            raise NoSuchProcess(process.pid)

        return inner_pid

    # If we weren't able to find the process' nspid, it must have been killed while searching (we only search
    # `/proc/pid/sched` files, and they exist as long as the process is running (including zombie processes)
    assert not process.is_running(), f"Process {process.pid} is running, but we failed to find his nspid"

    raise NoSuchProcess(process.pid)


def is_same_ns(process: Union[Process, int], nstype: str, process2: Union[Process, int] = None) -> bool:
    if isinstance(process, int):
        process = Process(process)
    if isinstance(process2, int):
        process2 = Process(process2)
    elif process2 is None:
        process2 = Process()  # `self`

    try:
        return _get_process_ns_inode(process, nstype) == _get_process_ns_inode(process2, nstype)
    except UnsupportedNamespaceError:
        # The namespace does not exist in this kernel, hence the two processes are logically in the same namespace
        return True


def _get_process_ns_inode(process: Process, nstype: str):
    try:
        ns_inode = os.stat(f"/proc/{process.pid}/ns/{nstype}").st_ino
    except FileNotFoundError as e:
        if process.is_running():
            raise UnsupportedNamespaceError(nstype) from e
        else:
            raise NoSuchProcess(process.pid) from e

    # If the process isn't running, we checked the wrong one
    if not process.is_running():
        raise NoSuchProcess(process.pid)

    return ns_inode


def run_in_ns(
    nstypes: List[str], callback: Callable[[], T], target_pid: int = 1, passthrough_exception: bool = False
) -> T:
    """
    Runs a callback in a new thread, switching to a set of the namespaces of a target process before
    doing so.

    Needed initially for switching mount namespaces, because we can't setns(CLONE_NEWNS) in a multithreaded
    program (unless we unshare(CLONE_NEWNS) before). so, we start a new thread, unshare() & setns() it,
    run our callback and then stop the thread (so we don't keep unshared threads running around).
    For other namespace types, we use this function to execute callbacks without changing the namespaces
    for the core threads.

    By default, run stuff in init NS. You can pass 'target_pid' to run in the namespace of that process.
    """

    # make sure "mnt" is last, once we change it our /proc is gone
    nstypes = sorted(nstypes, key=lambda ns: 1 if ns == "mnt" else 0)

    ret: Union[T, _Sentinel] = _SENTINEL
    exc: Optional[BaseException] = None

    def _switch_and_run():
        try:
            global libc
            if libc is None:
                libc = ctypes.CDLL("libc.so.6")

            for nstype in nstypes:
                if not is_same_ns(target_pid, nstype):
                    flag = NsType[nstype].value
                    if libc.unshare(flag) != 0:
                        raise ValueError(f"Failed to unshare({nstype})")

                    with open(f"/proc/{target_pid}/ns/{nstype}", "r") as nsf:
                        if libc.setns(nsf.fileno(), flag) != 0:
                            raise ValueError(f"Failed to setns({nstype}) (to pid {target_pid})")

            nonlocal ret
            ret = callback()
        except BaseException as e:
            # save the exception so we can re-raise it in the calling thread
            nonlocal exc
            exc = e

    t = Thread(target=_switch_and_run)
    t.start()
    t.join()

    if isinstance(ret, _Sentinel):
        assert exc is not None
        if passthrough_exception:
            raise exc
        else:
            raise Exception("run_in_ns execution failed") from exc
    else:
        assert exc is None
        return ret


def get_mnt_ns_ancestor(process: Process) -> Process:
    """
    Gets the topmost ancestor of "process" that runs in the same mount namespace of "process".
    """
    while True:
        parent = process.parent()
        if parent is None:  # topmost ancestor?
            return process

        if not is_same_ns(process.pid, "mnt", parent.pid):
            return process

        process = parent


def is_running_in_init_pid() -> bool:
    """
    Check if we're running in the init PID namespace.

    This check is implemented by checking if PID 2 is running, and if it's named "kthreadd"
    which is the kernel thread from which kernel threads are forked. It's always PID 2 and
    we should always see it in the init NS. If we don't have a PID 2 running, or if it's not named
    kthreadd, then we're not in the init PID NS.
    """
    try:
        p = Process(2)
    except NoSuchProcess:
        return False
    else:
        # technically, funny processes can name themselves "kthreadd", causing this check to pass in a non-init NS.
        # but we don't need to handle such extreme cases, I think.
        return p.name() == "kthreadd"


def get_proc_root_path(process: Process, from_ancestor: bool = True) -> str:
    """
    Gets /proc/<pid>/root of a given process, then a file can be read from the host mnt ns.
    """
    return f"/proc/{get_mnt_ns_ancestor(process).pid if from_ancestor else process.pid}/root"


def resolve_host_path(process: Process, ns_path: str, from_ancestor: bool = True) -> str:
    """
    Get a path in the host mount namespace pointing to path in process mount namespace.
    """
    return resolve_proc_root_links(get_proc_root_path(process, from_ancestor=from_ancestor), ns_path)


def get_host_pid(nspid: int, container_id: str) -> Optional[int]:
    assert len(container_id) == 64, f"Invalid container id {container_id!r}"

    pid_namespace = ""
    running_processes = list(process_iter())

    # Get the pid namespace of the given container
    for process in running_processes:
        try:
            if container_id == get_process_container_id(process):
                pid_namespace = os.readlink(f"/proc/{process.pid}/ns/pid")
                break
        except (FileNotFoundError, NoSuchProcess):
            continue

    if not pid_namespace:
        return None

    # Here we need to find the process by comparing pid namespaces and not by comparing cgroups, because
    # technically a process that's running in a container pid namespace doesn't have to share its cgroup
    for process in running_processes:
        try:
            if os.readlink(f"/proc/{process.pid}/ns/pid") == pid_namespace and get_process_nspid(process) == nspid:
                return process.pid
        except (FileNotFoundError, NoSuchProcess):
            continue

    return None
