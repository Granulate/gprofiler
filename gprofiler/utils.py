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
import random
import re
import shutil
import socket
import string
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from subprocess import CompletedProcess, Popen, TimeoutExpired
from tempfile import TemporaryDirectory
from threading import Event, Thread
from typing import Callable, Iterator, List, Optional, Tuple, Union

import distro  # type: ignore
import getmac
import importlib_resources
import psutil
from psutil import Process

from gprofiler.exceptions import (
    CalledProcessError,
    ProcessStoppedException,
    ProgramMissingException,
    StopEventSetException,
)
from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)

TEMPORARY_STORAGE_PATH = "/tmp/gprofiler_tmp"

gprofiler_mutex: Optional[socket.socket]
hostname: Optional[str] = None


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


def get_process_nspid(pid: int) -> Optional[int]:
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            fields = line.split()
            if fields[0] == "NSpid:":
                return int(fields[-1])

    # old kernel (pre 4.1) with no NSpid.
    # TODO if needed, this can be implemented for pre 4.1, by reading all /proc/pid/sched files as
    # seen by the PID NS; they expose the init NS PID (due to a bug fixed in 4.14~), and we can get the NS PID
    # from the listing of those files itself.
    return None


def start_process(cmd: Union[str, List[str]], via_staticx: bool, **kwargs) -> Popen:
    cmd_text = " ".join(cmd) if isinstance(cmd, list) else cmd
    logger.debug(f"Running command: ({cmd_text})")
    if isinstance(cmd, str):
        cmd = [cmd]

    env = kwargs.pop("env", None)
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
        else:
            # explicitly remove our directory from LD_LIBRARY_PATH
            env = env if env is not None else os.environ.copy()
            env.update({"LD_LIBRARY_PATH": ""})

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
    check: bool = True,
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
    if check and retcode != 0:
        raise CalledProcessError(retcode, process.args, output=stdout, stderr=stderr)
    return result


def pgrep_exe(match: str) -> Iterator[Process]:
    pattern = re.compile(match)
    for process in psutil.process_iter():
        try:
            if pattern.match(process.exe()):
                yield process
        except psutil.NoSuchProcess:  # process might have died meanwhile
            continue


def pgrep_maps(match: str) -> List[Process]:
    # this is much faster than iterating over processes' maps with psutil.
    result = run_process(
        f"grep -lP '{match}' /proc/*/maps",
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
        if not (line.startswith(b"grep: /proc/") and line.endswith(b"/maps: No such file or directory")):
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
    assert s.startswith(prefix), f"{s} doesn't start with {prefix}"
    return s[len(prefix) :]


def touch_path(path: str, mode: int) -> None:
    Path(path).touch()
    # chmod() afterwards (can't use 'mode' in touch(), because it's affected by umask)
    os.chmod(path, mode)


def remove_path(path: str, missing_ok: bool = False) -> None:
    # backporting missing_ok, available only from 3.8
    try:
        Path(path).unlink()
    except FileNotFoundError:
        if not missing_ok:
            raise


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


def get_libc_version() -> Tuple[str, str]:
    # platform.libc_ver fails for musl, sadly (produces empty results).
    # so we'll run "ldd --version" and extract the version string from it.
    # not passing "encoding"/"text" - this runs in a different mount namespace, and Python fails to
    # load the files it needs for those encodings (getting LookupError: unknown encoding: ascii)
    def decode_libc_version(version: bytes) -> str:
        return version.decode("utf-8", errors="replace")

    ldd_version = run_process(
        ["ldd", "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, suppress_log=True, check=False
    ).stdout
    # catches GLIBC & EGLIBC
    m = re.search(br"GLIBC (.*?)\)", ldd_version)
    if m is not None:
        return "glibc", decode_libc_version(m.group(1))
    # catches GNU libc
    m = re.search(br"\(GNU libc\) (.*?)\n", ldd_version)
    if m is not None:
        return "glibc", decode_libc_version(m.group(1))
    # musl
    m = re.search(br"musl libc.*?\nVersion (.*?)\n", ldd_version, re.M)
    if m is not None:
        return "musl", decode_libc_version(m.group(1))

    return "unknown", decode_libc_version(ldd_version)


def get_run_mode_and_deployment_type() -> Tuple[str, str]:
    if os.getenv("GPROFILER_IN_K8S") is not None:  # set in k8s/gprofiler.yaml
        return "k8s", "k8s"
    elif os.getenv("GPROFILER_IN_CONTAINER") is not None:  # set by our Dockerfile
        return "container", "containers"
    elif os.getenv("STATICX_BUNDLE_DIR") is not None:  # set by staticx
        return "standalone_executable", "instances"
    else:
        return "local_python", "instances"

def run_in_ns(nstypes: List[str], callback: Callable[[], None], target_pid: int = 1) -> None:
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

    def _switch_and_run():
        libc = ctypes.CDLL("libc.so.6")
        for nstype in nstypes:
            if not is_same_ns(target_pid, nstype):
                flag = {
                    "mnt": 0x00020000,  # CLONE_NEWNS
                    "net": 0x40000000,  # CLONE_NEWNET
                    "pid": 0x20000000,  # CLONE_NEWPID
                    "uts": 0x04000000,  # CLONE_NEWUTS
                }[nstype]
                if libc.unshare(flag) != 0:
                    raise ValueError(f"Failed to unshare({nstype})")

                with open(f"/proc/{target_pid}/ns/{nstype}", "r") as nsf:
                    if libc.setns(nsf.fileno(), flag) != 0:
                        raise ValueError(f"Failed to setns({nstype}) (to pid {target_pid})")

        callback()

    t = Thread(target=_switch_and_run)
    t.start()
    t.join()


def get_local_ip():
    try:
        local_ips = [ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if not ip.startswith("127.")]
    except Exception:
        local_ips = []
    if not local_ips:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 53))
            local_ips.append(s.getsockname()[0])
        finally:
            s.close()
    return local_ips[0] if local_ips else "unknown"


def _initialize_system_info():
    # initialized first
    global hostname
    hostname = "<unknown>"
    distribution = "unknown"
    libc_version = "unknown"
    mac_address = "unknown"
    local_ip = "unknown"
    boot_time_ms = 0

    # move to host mount NS for distro & ldd.
    # now, distro will read the files on host.
    # also move to host UTS NS for the hostname.
    def get_infos():
        nonlocal distribution, libc_version, boot_time_ms, mac_address, local_ip
        global hostname

        try:
            distribution = distro.linux_distribution()
        except Exception:
            logger.exception("Failed to get distribution")

        try:
            libc_version = get_libc_version()
        except Exception:
            logger.exception("Failed to get libc version")

        try:
            hostname = socket.gethostname()
        except Exception:
            logger.exception("Failed to get hostname")

        try:
            boot_time_ms = round(time.monotonic() * 1000)
        except Exception:
            logger.exception("Failed to get the system boot time")

        try:
            mac_address = getmac.get_mac_address()
        except Exception:
            logger.exception("Failed to get MAC address")

        try:
            local_ip = get_local_ip()
        except Exception:
            logger.exception("Failed to get the local IP")

    run_in_ns(["mnt", "uts"], get_infos)

    return hostname, distribution, libc_version, boot_time_ms, mac_address, local_ip


@dataclass
class SystemInfo:
    python_version: str
    run_mode: str
    deployment_type: str
    kernel_release: str
    kernel_version: str
    system_name: str
    processors: int
    memory_capacity_mb: int
    host_name: str
    os_name: str
    os_release: str
    os_codename: str
    libc_type: str
    libc_version: str
    hardware_type: str
    pid: int
    spawn_uptime_ms: int
    mac: str
    private_ip: str

    def get_dict(self):
        return self.__dict__.copy()


def get_system_info() -> SystemInfo:
    hostname, distribution, libc_tuple, boot_time_ms, mac_address, local_ip = _initialize_system_info()
    libc_type, libc_version = libc_tuple
    os_name, os_release, os_codename = distribution
    uname = platform.uname()
    cpu_count = os.cpu_count()
    cpu_count = cpu_count if cpu_count is not None else 0
    run_mode, deployment_type = get_run_mode_and_deployment_type()
    return SystemInfo(
        python_version=sys.version,
        run_mode=run_mode,
        deployment_type=deployment_type,
        kernel_release=uname.release,
        kernel_version=uname.version,
        system_name=uname.system,
        processors=cpu_count,
        memory_capacity_mb=round(psutil.virtual_memory().total / 1024 / 1024),
        host_name=hostname,
        os_name=os_name,
        os_release=os_release,
        os_codename=os_codename,
        libc_type=libc_type,
        libc_version=libc_version,
        hardware_type=uname.machine,
        pid=os.getpid(),
        spawn_uptime_ms=boot_time_ms,
        mac=mac_address,
        private_ip=local_ip,
    )


def log_system_info() -> None:
    system_info = get_system_info()
    logger.info(f"gProfiler Python version: {system_info.python_version}")
    logger.info(f"gProfiler deployment mode: {system_info.run_mode}")
    logger.info(f"Kernel uname release: {system_info.kernel_release}")
    logger.info(f"Kernel uname version: {system_info.kernel_version}")
    logger.info(f"Total CPUs: {system_info.processors}")
    logger.info(f"Total RAM: {system_info.memory_capacity_mb / (1 << 20):.2f} GB")
    logger.info(f"Linux distribution: {system_info.os_name} | {system_info.os_release} | {system_info.os_codename}")
    logger.info(f"libc version: {system_info.libc_type}-{system_info.libc_version}")
    logger.info(f"Hostname: {system_info.host_name}")


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


def is_running_in_init_pid() -> bool:
    """
    Check if we're running in the init PID namespace.

    This check is implemented by checking if PID 2 is running, and if it's named "kthreadd"
    which is the kernel thread from which kernel threads are forked. It's always PID 2 and
    we should always see it in the init NS. If we don't have a PID 2 running, or if it's not named
    kthreadd, then we're not in the init PID NS.
    """
    try:
        p = psutil.Process(2)
    except psutil.NoSuchProcess:
        return False
    else:
        # technically, funny processes can name themselves "kthreadd", causing this check to pass in a non-init NS.
        # but we don't need to handle such extreme cases, I think.
        return p.name() == "kthreadd"


def limit_frequency(limit: Optional[int], requested: int, msg_header: str, runtime_logger: logging.LoggerAdapter):
    if limit is not None and requested > limit:
        runtime_logger.warning(
            f"{msg_header}: Requested frequency ({requested}) is higher than the limit {limit}, "
            f"limiting the frequency to the limit ({limit})"
        )
        return limit

    return requested


def get_hostname() -> str:
    assert hostname is not None, "hostname not initialized!"
    return hostname


def random_prefix() -> str:
    return ''.join(random.choice(string.ascii_letters) for _ in range(16))
