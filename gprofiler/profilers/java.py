#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import functools
import json
import os
import re
import secrets
import signal
from enum import Enum
from pathlib import Path
from subprocess import CompletedProcess
from threading import Event, Lock
from types import TracebackType
from typing import Any, Dict, List, Optional, Sequence, Set, Type, TypeVar

import psutil
from granulate_utils.java import (
    CONTAINER_INFO_REGEX,
    DETECTED_JAVA_PROCESSES_REGEX,
    NATIVE_FRAMES_REGEX,
    SIGINFO_REGEX,
    VM_INFO_REGEX,
    JvmVersion,
    is_java_fatal_signal,
    java_exit_code_to_signo,
    locate_hotspot_error_file,
    parse_jvm_version,
)
from granulate_utils.linux import proc_events
from granulate_utils.linux.kernel_messages import KernelMessage
from granulate_utils.linux.ns import get_proc_root_path, get_process_nspid, resolve_proc_root_links, run_in_ns
from granulate_utils.linux.oom import get_oom_entry
from granulate_utils.linux.process import get_mapped_dso_elf_id, is_musl, is_process_running, process_exe
from granulate_utils.linux.signals import get_signal_entry
from packaging.version import Version
from psutil import Process

from gprofiler import merge
from gprofiler.exceptions import CalledProcessError, CalledProcessTimeoutError, NoRwExecDirectoryFoundError
from gprofiler.gprofiler_types import (
    ProcessToProfileData,
    ProfileData,
    StackToSampleCount,
    integer_range,
    positive_integer,
)
from gprofiler.kernel_messages import get_kernel_messages_provider
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import application_identifiers
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.profilers.profiler_base import SpawningProcessProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import (
    GPROFILER_DIRECTORY_NAME,
    TEMPORARY_STORAGE_PATH,
    pgrep_maps,
    remove_path,
    remove_prefix,
    resource_path,
    run_process,
    touch_path,
    wait_event,
)
from gprofiler.utils.fs import is_rw_exec_dir, safe_copy
from gprofiler.utils.perf import can_i_use_perf_events
from gprofiler.utils.process import is_process_basename_matching, process_comm, search_proc_maps

logger = get_logger_adapter(__name__)

libap_copy_lock = Lock()

# directories we check for rw,exec as candidates for libasyncProfiler.so placement.
POSSIBLE_AP_DIRS = (
    TEMPORARY_STORAGE_PATH,
    f"/run/{GPROFILER_DIRECTORY_NAME}",
    f"/opt/{GPROFILER_DIRECTORY_NAME}",
)


def frequency_to_ap_interval(frequency: int) -> int:
    # async-profiler accepts interval between samples (nanoseconds)
    return int((1 / frequency) * 1_000_000_000)


@functools.lru_cache(maxsize=1024)
def is_musl_cached(process: Process) -> bool:
    return is_musl(process)


JAVA_SAFEMODE_ALL = "all"  # magic value for *all* options from JavaSafemodeOptions


class JavaSafemodeOptions(str, Enum):
    # a profiled process was OOM-killed and we saw it in the kernel log
    PROFILED_OOM = "profiled-oom"
    # a profiled process was signaled:
    # * fatally signaled and we saw it in the kernel log
    # * we saw an exit code of signal in a proc_events event.
    PROFILED_SIGNALED = "profiled-signaled"
    # hs_err file was written for a profiled process
    HSERR = "hserr"
    # a process was OOM-killed and we saw it in the kernel log
    GENERAL_OOM = "general-oom"
    # a process was fatally signaled and we saw it in the kernel log
    GENERAL_SIGNALED = "general-signaled"
    # we saw the PID of a profiled process in the kernel logs
    PID_IN_KERNEL_MESSAGES = "pid-in-kernel-messages"
    # employ extended version checks before deciding to profile
    # see _is_jvm_profiling_supported() docs for more information
    JAVA_EXTENDED_VERSION_CHECKS = "java-extended-version-checks"
    # refuse profiling if async-profiler is already loaded (and not by gProfiler)
    # in the target process
    AP_LOADED_CHECK = "ap-loaded-check"


JAVA_SAFEMODE_ALL_OPTIONS = [o.value for o in JavaSafemodeOptions]
JAVA_SAFEMODE_DEFAULT_OPTIONS = [
    JavaSafemodeOptions.PROFILED_OOM.value,
    JavaSafemodeOptions.PROFILED_SIGNALED.value,
    JavaSafemodeOptions.HSERR.value,
]

JAVA_ASYNC_PROFILER_DEFAULT_SAFEMODE = 64  # StackRecovery.JAVA_STATE

SUPPORTED_AP_MODES = ["cpu", "itimer"]


class JattachExceptionBase(CalledProcessError):
    def __init__(
        self, returncode: int, cmd: Any, stdout: Any, stderr: Any, target_pid: int, ap_log: str, is_loaded: bool
    ):
        super().__init__(returncode, cmd, stdout, stderr)
        self._target_pid = target_pid
        self._ap_log = ap_log
        self.is_loaded = is_loaded

    def __str__(self) -> str:
        ap_log = self._ap_log.strip()
        if not ap_log:
            ap_log = "(empty)"
        loaded_msg = f"async-profiler DSO was{'' if self.is_loaded else ' not'} loaded"
        return super().__str__() + f"\nJava PID: {self._target_pid}\n{loaded_msg}\nasync-profiler log:\n{ap_log}"

    def get_ap_log(self) -> str:
        return self._ap_log


class JattachException(JattachExceptionBase):
    pass


# doesn't extend JattachException itself, we're not just a jattach error, we're
# specifically the timeout one.
class JattachTimeout(JattachExceptionBase):
    def __init__(
        self,
        returncode: int,
        cmd: Any,
        stdout: Any,
        stderr: Any,
        target_pid: int,
        ap_log: str,
        is_loaded: bool,
        timeout: int,
    ):
        super().__init__(returncode, cmd, stdout, stderr, target_pid, ap_log, is_loaded)
        self._timeout = timeout

    def __str__(self) -> str:
        return super().__str__() + (
            f"\njattach timed out (timeout was {self._timeout} seconds);"
            " you can increase it with the --java-jattach-timeout parameter."
        )


class JattachSocketMissingException(JattachExceptionBase):
    def __str__(self) -> str:
        # the attach listener is initialized once, then it is marked as initialized:
        # (https://github.com/openjdk/jdk/blob/3d07b3c7f01b60ff4dc38f62407c212b48883dbf/src/hotspot/share/services/attachListener.cpp#L388)
        # and will not be initialized again:
        # https://github.com/openjdk/jdk/blob/3d07b3c7f01b60ff4dc38f62407c212b48883dbf/src/hotspot/os/linux/attachListener_linux.cpp#L509
        # since openjdk 2870c9d55efe, the attach socket will be recreated even when removed (and this exception
        # won't happen).
        return super().__str__() + (
            "\nJVM attach socket is missing and jattach could not create it. It has most"
            " likely been removed; the process has to be restarted for a new socket to be created."
        )


def is_java_basename(process: Process) -> bool:
    return is_process_basename_matching(process, r"^java$")


_JAVA_VERSION_TIMEOUT = 5

_JAVA_VERSION_CACHE_MAX = 1024


# process is hashable and the same process instance compares equal
@functools.lru_cache(maxsize=_JAVA_VERSION_CACHE_MAX)
def get_java_version(process: Process, stop_event: Event) -> str:
    # make sure we're only called for "java" processes, otherwise running "-version" makes no sense.
    # our callers should check for it.
    assert is_java_basename(process), f"expected java, found {process!r}"

    nspid = get_process_nspid(process.pid)

    # this has the benefit of working even if the Java binary was replaced, e.g due to an upgrade.
    # in that case, the libraries would have been replaced as well, and therefore we're actually checking
    # the version of the now installed Java, and not the running one.
    # but since this is used for the "JDK type" check, it's good enough - we don't expect that to change.
    # this whole check, however, is growing to be too complex, and we should consider other approaches
    # for it:
    # 1. purely in async-profiler - before calling any APIs that might harm blacklisted JDKs, we can
    #    check the JDK type in async-profiler itself.
    # 2. assume JDK type by the path, e.g the "java" Docker image has
    #    "/usr/lib/jvm/java-8-openjdk-amd64/jre/bin/java" which means "OpenJDK". needs to be checked for
    #    other JDK types.
    java_path = f"/proc/{nspid}/exe"

    def _run_java_version() -> "CompletedProcess[bytes]":
        return run_process(
            [
                java_path,
                "-version",
            ],
            stop_event=stop_event,
            timeout=_JAVA_VERSION_TIMEOUT,
        )

    # doesn't work without changing PID NS as well (I'm getting ENOENT for libjli.so)
    # Version is printed to stderr
    return run_in_ns(["pid", "mnt"], _run_java_version, process.pid).stderr.decode().strip()


def get_java_version_logged(process: Process, stop_event: Event) -> str:
    java_version = get_java_version(process, stop_event)
    logger.debug("java -version output", java_version_output=java_version, pid=process.pid)
    return java_version


class JavaMetadata(ApplicationMetadata):
    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        if is_java_basename(process):
            version = get_java_version(process, self._stop_event)
        else:
            version = "not /java"
        # libjvm elfid - we care only about libjvm, not about the java exe itself which is a just small program
        # that loads other libs.
        libjvm_elfid = get_mapped_dso_elf_id(process, "/libjvm")

        metadata = {"java_version": version, "libjvm_elfid": libjvm_elfid}

        metadata.update(super().make_application_metadata(process))
        return metadata


@functools.lru_cache(maxsize=1)
def jattach_path() -> str:
    return resource_path("java/jattach")


@functools.lru_cache(maxsize=1)
def fdtransfer_path() -> str:
    return resource_path("java/fdtransfer")


@functools.lru_cache(maxsize=1)
def get_ap_version() -> str:
    return Path(resource_path("java/async-profiler-version")).read_text()


T = TypeVar("T", bound="AsyncProfiledProcess")


class AsyncProfiledProcess:
    """
    Represents a process profiled with async-profiler.
    """

    FORMAT_PARAMS = "ann,sig"
    OUTPUT_FORMAT = "collapsed"
    OUTPUTS_MODE = 0o622  # readable by root, writable by all

    # timeouts in seconds
    _FDTRANSFER_TIMEOUT = 10
    _JATTACH_TIMEOUT = 30  # higher than jattach's timeout

    _DEFAULT_MCACHE = 30  # arbitrarily chosen, not too high & not too low.

    def __init__(
        self,
        process: Process,
        storage_dir: str,
        stop_event: Event,
        buildids: bool,
        mode: str,
        ap_safemode: int,
        ap_args: str,
        jattach_timeout: int = _JATTACH_TIMEOUT,
        mcache: int = 0,
    ):
        self.process = process
        self._stop_event = stop_event
        # access the process' root via its topmost parent/ancestor which uses the same mount namespace.
        # this allows us to access the files after the process exits:
        # * for processes that run in host mount NS - their ancestor is always available (it's going to be PID 1)
        # * for processes that run in a container, and the container remains running after they exit - hence, the
        #   ancestor is still alive.
        # there is a hidden assumption here that neither the ancestor nor the process will change their mount
        # namespace. I think it's okay to assume that.
        self._process_root = get_proc_root_path(process)
        self._cmdline = process.cmdline()
        self._cwd = process.cwd()
        self._nspid = get_process_nspid(self.process.pid)

        # not using storage_dir for AP itself on purpose: this path should remain constant for the lifetime
        # of the target process, so AP is loaded exactly once (if we have multiple paths, AP can be loaded
        # multiple times into the process)
        # without depending on storage_dir here, we maintain the same path even if gProfiler is re-run,
        # because storage_dir changes between runs.
        # we embed the async-profiler version in the path, so future gprofiler versions which use another version
        # of AP case use it (will be loaded as a different DSO)
        self._ap_dir_host = os.path.join(
            self._find_rw_exec_dir(POSSIBLE_AP_DIRS),
            f"async-profiler-{get_ap_version()}",
            "musl" if self._is_musl() else "glibc",
        )

        self._libap_path_host = os.path.join(self._ap_dir_host, "libasyncProfiler.so")
        self._libap_path_process = remove_prefix(self._libap_path_host, self._process_root)

        # for other purposes - we can use storage_dir.
        self._storage_dir = storage_dir
        self._storage_dir_host = resolve_proc_root_links(self._process_root, self._storage_dir)

        self._output_path_host = os.path.join(self._storage_dir_host, f"async-profiler-{self.process.pid}.output")
        self._output_path_process = remove_prefix(self._output_path_host, self._process_root)
        self._log_path_host = os.path.join(self._storage_dir_host, f"async-profiler-{self.process.pid}.log")
        self._log_path_process = remove_prefix(self._log_path_host, self._process_root)

        self._buildids = buildids
        assert mode in ("cpu", "itimer"), f"unexpected mode: {mode}"
        self._mode = mode
        self._fdtransfer_path = f"@async-profiler-{process.pid}-{secrets.token_hex(10)}" if mode == "cpu" else None
        self._ap_safemode = ap_safemode
        self._ap_args = ap_args
        self._jattach_timeout = jattach_timeout
        self._mcache = mcache

    def _find_rw_exec_dir(self, available_dirs: Sequence[str]) -> str:
        """
        Find a rw & executable directory (in the context of the process) where we can place libasyncProfiler.so
        and the target process will be able to load it.
        """
        for d in available_dirs:
            full_dir = resolve_proc_root_links(self._process_root, d)
            if is_rw_exec_dir(full_dir):
                return full_dir
        else:
            raise NoRwExecDirectoryFoundError(f"Could not find a rw & exec directory out of {available_dirs}!")

    def __enter__(self: T) -> T:
        os.makedirs(self._ap_dir_host, 0o755, exist_ok=True)
        os.makedirs(self._storage_dir_host, 0o755, exist_ok=True)

        self._check_disk_requirements()

        # make out & log paths writable for all, so target process can write to them.
        # see comment on TemporaryDirectoryWithMode in GProfiler.__init__.
        touch_path(self._output_path_host, self.OUTPUTS_MODE)
        self._recreate_log()
        # copy libasyncProfiler.so if needed
        self._copy_libap()

        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_ctb: Optional[TracebackType],
    ) -> None:
        # ignore_errors because we are deleting paths via /proc/pid/root - and the pid
        # we're using might have gone down already.
        # remove them as best effort.
        remove_path(self._output_path_host, missing_ok=True)
        remove_path(self._log_path_host, missing_ok=True)

    def _existing_realpath(self, path: str) -> Optional[str]:
        """
        Return path relative to process working directory if it exists. Otherwise return None.
        """
        if not path.startswith("/"):
            # relative path
            path = f"{self._cwd}/{path}"
        path = resolve_proc_root_links(self._process_root, path)
        return path if os.path.exists(path) else None

    def locate_hotspot_error_file(self) -> Optional[str]:
        for path in locate_hotspot_error_file(self._nspid, self._cmdline):
            realpath = self._existing_realpath(path)
            if realpath is not None:
                return realpath
        return None

    def _is_musl(self) -> bool:
        # Is target process musl-based?
        return is_musl_cached(self.process)

    def _copy_libap(self) -> None:
        # copy *is* racy with respect to other processes running in the same namespace, because they all use
        # the same directory for libasyncProfiler.so.
        # therefore, we need to synchronize copies from different threads that profile different processes.
        if os.path.exists(self._libap_path_host):
            # all good
            return

        with libap_copy_lock:
            if not os.path.exists(self._libap_path_host):
                # atomically copy it
                libap_resource = resource_path(
                    os.path.join("java", "musl" if self._is_musl() else "glibc", "libasyncProfiler.so")
                )
                os.chmod(
                    libap_resource, 0o755
                )  # make it accessible for all; needed with PyInstaller, which extracts files as 0700
                safe_copy(libap_resource, self._libap_path_host)

    def _recreate_log(self) -> None:
        touch_path(self._log_path_host, self.OUTPUTS_MODE)

    def _check_disk_requirements(self) -> None:
        """
        Avoid running if disk space is low, so we don't reach out-of-disk space situation because of profiling data.
        """
        free_disk = psutil.disk_usage(self._storage_dir_host).free
        required = 250 * 1024
        if free_disk < required:
            raise Exception(
                f"Not enough free disk space: {free_disk}kb left, {250 * 1024}kb"
                f" required (on path: {self._output_path_host!r}"
            )

    def _get_base_cmd(self) -> List[str]:
        return [
            jattach_path(),
            str(self.process.pid),
            "load",
            self._libap_path_process,
            "true",
        ]

    def _get_extra_ap_args(self) -> str:
        return f",{self._ap_args}" if self._ap_args else ""

    def _get_ap_output_args(self) -> str:
        return f",file={self._output_path_process},{self.OUTPUT_FORMAT},{self.FORMAT_PARAMS}"

    def _get_start_cmd(self, interval: int, ap_timeout: int) -> List[str]:
        return self._get_base_cmd() + [
            f"start,event={self._mode}"
            f"{self._get_ap_output_args()},interval={interval},"
            f"log={self._log_path_process}{',buildids' if self._buildids else ''}"
            f"{f',fdtransfer={self._fdtransfer_path}' if self._mode == 'cpu' else ''}"
            f",safemode={self._ap_safemode},timeout={ap_timeout}{self._get_extra_ap_args()}"
        ]

    def _get_stop_cmd(self, with_output: bool) -> List[str]:
        return self._get_base_cmd() + [
            f"stop,log={self._log_path_process},mcache={self._mcache}"
            + (self._get_ap_output_args() if with_output else "")
            + self._get_extra_ap_args()
        ]

    def _read_ap_log(self) -> str:
        if not os.path.exists(self._log_path_host):
            return "(log file doesn't exist)"

        log = Path(self._log_path_host)
        ap_log = log.read_text()
        # clean immediately so we don't mix log messages from multiple invocations.
        # this is also what AP's profiler.sh does.
        log.unlink()
        self._recreate_log()
        return ap_log

    def _run_async_profiler(self, cmd: List[str]) -> None:
        try:
            # kill jattach with SIGTERM if it hangs. it will go down
            run_process(cmd, stop_event=self._stop_event, timeout=self._jattach_timeout, kill_signal=signal.SIGTERM)
        except CalledProcessError as e:  # catches CalledProcessTimeoutError as well
            ap_log = self._read_ap_log()
            is_loaded = f" {self._libap_path_process}\n" in Path(f"/proc/{self.process.pid}/maps").read_text()

            args = e.returncode, e.cmd, e.stdout, e.stderr, self.process.pid, ap_log, is_loaded
            if isinstance(e, CalledProcessTimeoutError):
                raise JattachTimeout(*args, timeout=self._jattach_timeout) from None
            elif e.stderr == b"Could not start attach mechanism: No such file or directory\n":
                # this is true for jattach_hotspot
                raise JattachSocketMissingException(*args) from None
            else:
                raise JattachException(*args) from None
        else:
            logger.debug("async-profiler log", jattach_cmd=cmd, ap_log=self._read_ap_log())

    def _run_fdtransfer(self) -> None:
        """
        Start fdtransfer; it will fork & exit once ready, so we can continue with jattach.
        """
        assert self._fdtransfer_path is not None  # should be set if fdntransfer is invoked
        run_process(
            [fdtransfer_path(), self._fdtransfer_path, str(self.process.pid)],
            stop_event=self._stop_event,
            timeout=self._FDTRANSFER_TIMEOUT,
        )

    def start_async_profiler(self, interval: int, second_try: bool = False, ap_timeout: int = 0) -> bool:
        """
        Returns True if profiling was started; False if it was already started.
        ap_timeout defaults to 0, which means "no timeout" for AP (see call to startTimer() in profiler.cpp)
        """
        if self._mode == "cpu" and not second_try:
            self._run_fdtransfer()

        start_cmd = self._get_start_cmd(interval, ap_timeout)
        try:
            self._run_async_profiler(start_cmd)
            return True
        except JattachException as e:
            if e.is_loaded:
                if (
                    e.returncode == 200  # 200 == AP's COMMAND_ERROR
                    and e.get_ap_log() == "[ERROR] Profiler already started\n"
                ):
                    # profiler was already running
                    return False
            raise

    def stop_async_profiler(self, with_output: bool) -> None:
        self._run_async_profiler(self._get_stop_cmd(with_output))

    def read_output(self) -> Optional[str]:
        try:
            return Path(self._output_path_host).read_text()
        except FileNotFoundError:
            # perhaps it has exited?
            if not is_process_running(self.process):
                return None
            raise


@register_profiler(
    "Java",
    possible_modes=["ap", "disabled"],
    default_mode="ap",
    supported_archs=["x86_64", "aarch64"],
    profiler_arguments=[
        ProfilerArgument(
            "--java-async-profiler-buildids",
            dest="java_async_profiler_buildids",
            action="store_true",
            help="Embed buildid+offset in async-profiler native frames."
            " The added buildid+offset can be resolved & symbolicated in the Performance Studio."
            " This is useful if debug symbols are unavailable for the relevant DSOs (libjvm, libc, ...).",
        ),
        ProfilerArgument(
            "--java-no-version-check",
            dest="java_version_check",
            action="store_false",
            help="Skip the JDK version check (that is done before invoking async-profiler). See"
            " _is_jvm_profiling_supported() docs for more information.",
        ),
        ProfilerArgument(
            "--java-async-profiler-mode",
            dest="java_async_profiler_mode",
            choices=SUPPORTED_AP_MODES + ["auto"],
            default="auto",
            help="Select async-profiler's mode: 'cpu' (based on perf_events & fdtransfer), 'itimer' (no perf_events)"
            " or 'auto' (select 'cpu' if perf_events are available; otherwise 'itimer'). Defaults to '%(default)s'.",
        ),
        ProfilerArgument(
            "--java-async-profiler-safemode",
            dest="java_async_profiler_safemode",
            default=JAVA_ASYNC_PROFILER_DEFAULT_SAFEMODE,
            type=integer_range(0, 128),
            metavar="[0-127]",
            help="Controls the 'safemode' parameter passed to async-profiler. This is parameter denotes multiple"
            " bits that describe different stack recovery techniques which async-profiler uses (see StackRecovery"
            " enum in async-profiler's code, in profiler.cpp)."
            " Defaults to '%(default)s').",
        ),
        ProfilerArgument(
            "--java-async-profiler-args",
            dest="java_async_profiler_args",
            type=str,
            help="Additional arguments to pass directly to async-profiler (start & stop commands)",
        ),
        ProfilerArgument(
            "--java-safemode",
            dest="java_safemode",
            type=str,
            const=JAVA_SAFEMODE_ALL,
            nargs="?",
            default=",".join(JAVA_SAFEMODE_DEFAULT_OPTIONS),
            help="Sets the Java profiler safemode options. Default is: %(default)s.",
        ),
        ProfilerArgument(
            "--java-jattach-timeout",
            dest="java_jattach_timeout",
            type=positive_integer,
            default=AsyncProfiledProcess._JATTACH_TIMEOUT,
            help="Timeout for jattach operations (start/stop AP, etc)",
        ),
        ProfilerArgument(
            "--java-async-profiler-mcache",
            dest="java_async_profiler_mcache",
            # this is "unsigned char" in AP's code
            type=integer_range(0, 256),
            metavar="[0-255]",
            default=AsyncProfiledProcess._DEFAULT_MCACHE,
            help="async-profiler mcache option (defaults to %(default)s)",
        ),
        ProfilerArgument(
            "--java-collect-spark-app-name-as-appid",
            dest="java_collect_spark_app_name_as_appid",
            action="store_true",
            default=False,
            help="In case of Spark application executor process - add the name of the Spark application to the appid.",
        ),
    ],
)
class JavaProfiler(SpawningProcessProfilerBase):
    JDK_EXCLUSIONS: List[str] = []  # currently empty
    # Major -> (min version, min build number of version)
    MINIMAL_SUPPORTED_VERSIONS = {
        7: (Version("7.76"), 4),
        8: (Version("8.25"), 17),
        11: (Version("11.0.2"), 7),
        12: (Version("12.0.1"), 12),
        13: (Version("13.0.1"), 9),
        14: (Version("14"), 33),
        15: (Version("15.0.1"), 9),
        16: (Version("16"), 36),
        17: (Version("17.0.1"), 12),
    }

    # extra timeout seconds to add to the duration itself.
    # once the timeout triggers, AP remains stopped, so if it triggers before we tried to stop
    # AP ourselves, we'll be in messed up state. hence, we add 30s which is enough.
    _AP_EXTRA_TIMEOUT_S = 30

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Event,
        storage_dir: str,
        profile_spawned_processes: bool,
        java_async_profiler_buildids: bool,
        java_version_check: bool,
        java_async_profiler_mode: str,
        java_async_profiler_safemode: int,
        java_async_profiler_args: str,
        java_safemode: str,
        java_jattach_timeout: int,
        java_async_profiler_mcache: int,
        java_collect_spark_app_name_as_appid: bool,
        java_mode: str,
    ):
        assert java_mode == "ap", "Java profiler should not be initialized, wrong java_mode value given"
        super().__init__(frequency, duration, stop_event, storage_dir, profile_spawned_processes)

        self._interval = frequency_to_ap_interval(frequency)
        self._buildids = java_async_profiler_buildids
        # simple version check, and
        self._simple_version_check = java_version_check
        if not self._simple_version_check:
            logger.warning("Java version checks are disabled")
        self._init_ap_mode(java_async_profiler_mode)
        self._ap_safemode = java_async_profiler_safemode
        self._ap_args = java_async_profiler_args
        self._jattach_timeout = java_jattach_timeout
        self._ap_mcache = java_async_profiler_mcache
        self._collect_spark_app_name = java_collect_spark_app_name_as_appid
        self._init_java_safemode(java_safemode)
        self._should_profile = True
        # if set, profiling is disabled due to this safemode reason.
        self._safemode_disable_reason: Optional[str] = None
        self._want_to_profile_pids: Set[int] = set()
        self._profiled_pids: Set[int] = set()
        self._pids_to_remove: Set[int] = set()
        self._pid_to_java_version: Dict[int, Optional[str]] = {}
        self._kernel_messages_provider = get_kernel_messages_provider()
        self._enabled_proc_events_java = False
        self._ap_timeout = self._duration + self._AP_EXTRA_TIMEOUT_S
        self._metadata = JavaMetadata(self._stop_event)

    def _init_ap_mode(self, ap_mode: str) -> None:
        if ap_mode == "auto":
            ap_mode = "cpu" if can_i_use_perf_events() else "itimer"
            logger.debug("Auto selected AP mode", ap_mode=ap_mode)

        assert ap_mode in SUPPORTED_AP_MODES, f"unexpected ap mode: {ap_mode}"
        self._mode = ap_mode

    def _init_java_safemode(self, java_safemode: str) -> None:
        if java_safemode == JAVA_SAFEMODE_ALL:
            self._java_safemode = JAVA_SAFEMODE_ALL_OPTIONS
        else:
            # accept "" as empty, because sometimes people confuse and use --java-safemode="" in non-shell
            # environment (e.g DaemonSet args) and thus the "" isn't eaten by the shell.
            self._java_safemode = java_safemode.split(",") if (java_safemode and java_safemode != '""') else []

        assert all(
            o in JAVA_SAFEMODE_ALL_OPTIONS for o in self._java_safemode
        ), f"unknown options given in Java safemode: {self._java_safemode!r}"

        if self._java_safemode:
            logger.debug("Java safemode enabled", safemode=self._java_safemode)

        if JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS in self._java_safemode:
            assert self._simple_version_check, (
                "Java version checks are mandatory in"
                f" --java-safemode={JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS}"
            )

    def _disable_profiling(self, cause: str) -> None:
        if self._safemode_disable_reason is None and cause in self._java_safemode:
            logger.warning("Java profiling has been disabled, will avoid profiling any new java processes", cause=cause)
            self._safemode_disable_reason = cause

    def _profiling_skipped_profile(self, reason: str, comm: str) -> ProfileData:
        return ProfileData(self._profiling_error_stack("skipped", reason, comm), None, None)

    def _is_jvm_type_supported(self, java_version_cmd_output: str) -> bool:
        return all(exclusion not in java_version_cmd_output for exclusion in self.JDK_EXCLUSIONS)

    def _is_zing_vm_supported(self, jvm_version: JvmVersion) -> bool:
        # name is e.g Zing 64-Bit Tiered VM Zing22.04.1.0+1
        m = re.search(r"Zing(\d+)\.", jvm_version.name)
        if m is None:
            return False  # unknown

        major = m.group(1)
        if int(major) < 18:
            return False

        # Zing > 18 is assumed to support AsyncGetCallTrace per
        # https://github.com/jvm-profiling-tools/async-profiler/issues/153#issuecomment-452038960
        return True

    def _check_jvm_supported_extended(self, jvm_version: JvmVersion) -> bool:
        if jvm_version.version.major not in self.MINIMAL_SUPPORTED_VERSIONS:
            logger.warning("Unsupported JVM version", jvm_version=repr(jvm_version))
            return False
        min_version, min_build = self.MINIMAL_SUPPORTED_VERSIONS[jvm_version.version.major]
        if jvm_version.version < min_version:
            logger.warning("Unsupported JVM version", jvm_version=repr(jvm_version))
            return False
        elif jvm_version.version == min_version:
            if jvm_version.build < min_build:
                logger.warning("Unsupported JVM version", jvm_version=repr(jvm_version))
                return False

        return True

    def _check_jvm_supported_simple(self, process: Process, java_version_output: str, jvm_version: JvmVersion) -> bool:
        if not self._is_jvm_type_supported(java_version_output):
            logger.warning("Unsupported JVM type", java_version_output=java_version_output)
            return False

        # Zing checks
        if jvm_version.name.startswith("Zing"):
            if not self._is_zing_vm_supported(jvm_version):
                logger.warning("Unsupported Zing VM version", jvm_version=repr(jvm_version))
                return False

        # HS checks
        if jvm_version.name.startswith("OpenJDK"):
            if not jvm_version.version.major > 6:
                logger.warning("Unsupported HotSpot version", jvm_version=repr(jvm_version))
                return False

        return True

    def _is_jvm_profiling_supported(self, process: Process, exe: str, java_version_output: Optional[str]) -> bool:
        """
        This is the core "version check" function.
        We have 3 modes of operation:
        1. No checks at all - java-extended-version-checks is NOT present in --java-safemode, *and*
           --java-no-version-check is passed. In this mode we'll profile all JVMs.
        2. Default - neither java-extended-version-checks nor --java-no-version-check are passed,
           this mode is called "simple checks" and we run minimal checks - if profiled process is
           basenamed "java", we get the JVM version and make sure that for Zing, we attempt profiling
           only if Zing version is >18, and for HS, only if JDK>6. If process is not basenamed "java" we
           just profile it.
        3. Extended - java-extended-version-checks is passed, we only profile processes which are basenamed "java",
           who pass the criteria enforced by the default mode ("simple checks") and additionally all checks
           performed by _check_jvm_supported_extended().
        """
        if JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS in self._java_safemode:
            if java_version_output is None:  # we don't get the java version if the exe isn't "java"
                logger.warning(
                    "Non-java basenamed process (cannot get Java version), skipping... (disable "
                    f"--java-safemode={JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS} to profile it anyway)",
                    pid=process.pid,
                    exe=exe,
                )
                return False

            jvm_version = parse_jvm_version(java_version_output)
            if not self._check_jvm_supported_simple(process, java_version_output, jvm_version):
                return False

            if not self._check_jvm_supported_extended(jvm_version):
                logger.warning(
                    "Process running unsupported Java version, skipping..."
                    f" (disable --java-safemode={JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS}"
                    " to profile it anyway)",
                    pid=process.pid,
                    java_version_output=java_version_output,
                )
                return False
        else:
            if self._simple_version_check and java_version_output is not None:
                jvm_version = parse_jvm_version(java_version_output)
                if not self._check_jvm_supported_simple(process, java_version_output, jvm_version):
                    return False

        return True

    def _check_async_profiler_loaded(self, process: Process) -> bool:
        if JavaSafemodeOptions.AP_LOADED_CHECK not in self._java_safemode:
            # don't care
            return False

        for mmap in process.memory_maps():
            # checking only with GPROFILER_DIRECTORY_NAME and not TEMPORARY_STORAGE_PATH;
            # the resolved path of TEMPORARY_STORAGE_PATH might be different from TEMPORARY_STORAGE_PATH itself,
            # and in the mmap.path we're seeing the resolved path. it's a hassle to resolve it here - this
            # check is good enough, possibly only too strict, not too loose.
            if "libasyncProfiler.so" in mmap.path and f"/{GPROFILER_DIRECTORY_NAME}/" not in mmap.path:
                logger.warning(
                    "Non-gProfiler async-profiler is already loaded to the target process."
                    f" (disable --java-safemode={JavaSafemodeOptions.AP_LOADED_CHECK} to bypass this check)",
                    pid=process.pid,
                    ap_path=mmap.path,
                )
                return True

        return False

    def _profile_process(self, process: Process, duration: int, spawned: bool) -> ProfileData:
        comm = process_comm(process)
        exe = process_exe(process)
        # TODO we can get the "java" binary by extracting the java home from the libjvm path,
        # then check with that instead (if exe isn't java)
        if is_java_basename(process):
            java_version_output: Optional[str] = get_java_version_logged(process, self._stop_event)
        else:
            java_version_output = None

        if self._enabled_proc_events_java:
            self._want_to_profile_pids.add(process.pid)
            # there's no reliable way to get the underlying cache of get_java_version, otherwise
            # I'd just use it.
            if len(self._pid_to_java_version) > _JAVA_VERSION_CACHE_MAX:
                self._pid_to_java_version.clear()

            # This Java version might be used in _proc_exit_callback
            self._pid_to_java_version[process.pid] = java_version_output

        if self._safemode_disable_reason is not None:
            return self._profiling_skipped_profile(f"disabled due to {self._safemode_disable_reason}", comm)

        if not self._is_jvm_profiling_supported(process, exe, java_version_output):
            return self._profiling_skipped_profile("profiling this JVM is not supported", comm)

        if self._check_async_profiler_loaded(process):
            return self._profiling_skipped_profile("async-profiler is already loaded", comm)

        # track profiled PIDs only if proc_events are in use, otherwise there is no use in them.
        # TODO: it is possible to run in contexts where we're unable to use proc_events but are able to listen
        # on kernel messages. we can add another mechanism to track PIDs (such as, prune PIDs which have exited)
        # then use the kernel messages listener without proc_events.
        if self._enabled_proc_events_java:
            self._profiled_pids.add(process.pid)

        logger.info(f"Profiling{' spawned' if spawned else ''} process {process.pid} with async-profiler")
        app_metadata = self._metadata.get_metadata(process)
        appid = application_identifiers.get_java_app_id(process, self._collect_spark_app_name)

        with AsyncProfiledProcess(
            process,
            self._storage_dir,
            self._stop_event,
            self._buildids,
            self._mode,
            self._ap_safemode,
            self._ap_args,
            self._jattach_timeout,
            self._ap_mcache,
        ) as ap_proc:
            stackcollapse = self._profile_ap_process(ap_proc, comm, duration)

        return ProfileData(stackcollapse, appid, app_metadata)

    def _profile_ap_process(self, ap_proc: AsyncProfiledProcess, comm: str, duration: int) -> StackToSampleCount:
        started = ap_proc.start_async_profiler(self._interval, ap_timeout=self._ap_timeout)
        if not started:
            logger.info(f"Found async-profiler already started on {ap_proc.process.pid}, trying to stop it...")
            # stop, and try to start again. this might happen if AP & gProfiler go out of sync: for example,
            # gProfiler being stopped brutally, while AP keeps running. If gProfiler is later started again, it will
            # try to start AP again...
            # not using the "resume" action because I'm not sure it properly reconfigures all settings; while stop;start
            # surely does.
            ap_proc.stop_async_profiler(with_output=False)
            started = ap_proc.start_async_profiler(self._interval, second_try=True, ap_timeout=self._ap_timeout)
            if not started:
                raise Exception(
                    f"async-profiler is still running in {ap_proc.process.pid}, even after trying to stop it!"
                )

        try:
            wait_event(duration, self._stop_event, lambda: not is_process_running(ap_proc.process), interval=1)
        except TimeoutError:
            # Process still running. We will stop the profiler in finally block.
            pass
        else:
            # Process terminated, was it due to an error?
            self._check_hotspot_error(ap_proc)
            logger.debug(f"Profiled process {ap_proc.process.pid} exited before stopping async-profiler")
            # fall-through - try to read the output, since async-profiler writes it upon JVM exit.
        finally:
            if is_process_running(ap_proc.process):
                ap_proc.stop_async_profiler(True)

        output = ap_proc.read_output()
        if output is None:
            logger.warning(f"Profiled process {ap_proc.process.pid} exited before reading the output")
            return self._profiling_error_stack("error", "process exited before reading the output", comm)
        else:
            logger.info(f"Finished profiling process {ap_proc.process.pid}")
            return merge.parse_one_collapsed(output, comm)

    def _check_hotspot_error(self, ap_proc: AsyncProfiledProcess) -> None:
        pid = ap_proc.process.pid
        error_file = ap_proc.locate_hotspot_error_file()
        if not error_file:
            logger.debug(f"No Hotspot error log for pid {pid}")
            return

        contents = open(error_file).read()
        m = VM_INFO_REGEX.search(contents)
        vm_info = m[1] if m else ""
        m = SIGINFO_REGEX.search(contents)
        siginfo = m[1] if m else ""
        m = NATIVE_FRAMES_REGEX.search(contents)
        native_frames = m[1] if m else ""
        m = CONTAINER_INFO_REGEX.search(contents)
        container_info = m[1] if m else ""
        logger.warning(
            f"Found Hotspot error log for pid {pid} at {error_file}:\n"
            f"VM info: {vm_info}\n"
            f"siginfo: {siginfo}\n"
            f"native frames:\n{native_frames}\n"
            f"container info:\n{container_info}"
        )

        self._disable_profiling(JavaSafemodeOptions.HSERR)

    def _select_processes_to_profile(self) -> List[Process]:
        if self._safemode_disable_reason is not None:
            logger.debug("Java profiling has been disabled, skipping profiling of all java processes")
            # continue - _profile_process will return an appropriate error for each process selected for
            # profiling.
        return pgrep_maps(DETECTED_JAVA_PROCESSES_REGEX)

    def _should_profile_process(self, process: Process) -> bool:
        return search_proc_maps(process, DETECTED_JAVA_PROCESSES_REGEX) is not None

    def start(self) -> None:
        super().start()
        try:
            proc_events.register_exit_callback(self._proc_exit_callback)
        except Exception:
            logger.warning(
                "Failed to enable proc_events listener for exited Java processes"
                " (this does not prevent Java profiling)",
                exc_info=True,
            )
        else:
            self._enabled_proc_events_java = True

    def stop(self) -> None:
        if self._enabled_proc_events_java:
            proc_events.unregister_exit_callback(self._proc_exit_callback)
            self._enabled_proc_events_java = False
        super().stop()

    def _proc_exit_callback(self, tid: int, pid: int, exit_code: int) -> None:
        # Notice that we only check the exit code of the main thread here.
        # It's assumed that an error in any of the Java threads will be reflected in the exit code of the main thread.
        if tid in self._want_to_profile_pids:
            self._pids_to_remove.add(tid)
            java_version_output = self._pid_to_java_version.get(tid)

            signo = java_exit_code_to_signo(exit_code)
            if signo is None:
                # not a signal, do not report
                return

            if tid in self._profiled_pids:
                logger.warning(
                    "async-profiled Java process exited with signal",
                    pid=tid,
                    signal=signo,
                    java_version_output=java_version_output,
                )

                if is_java_fatal_signal(signo):
                    self._disable_profiling(JavaSafemodeOptions.PROFILED_SIGNALED)
            else:
                # this is a process that we wanted to profile, but didn't profile due to safemode / any other reason.
                logger.debug(
                    "Non-profiled Java process exited with signal",
                    pid=tid,
                    signal=signo,
                    java_version_output=java_version_output,
                )

    def _handle_kernel_messages(self, messages: List[KernelMessage]) -> None:
        for message in messages:
            _, _, text = message
            oom_entry = get_oom_entry(text)
            if oom_entry and oom_entry.pid in self._profiled_pids:
                logger.warning("Profiled Java process OOM", oom=json.dumps(oom_entry._asdict()))
                self._disable_profiling(JavaSafemodeOptions.PROFILED_OOM)
                continue

            signal_entry = get_signal_entry(text)
            if signal_entry is not None and signal_entry.pid in self._profiled_pids:
                logger.warning("Profiled Java process fatally signaled", signal=json.dumps(signal_entry._asdict()))
                self._disable_profiling(JavaSafemodeOptions.PROFILED_SIGNALED)
                continue

            # paranoia - in safemode, stop Java profiling upon any OOM / fatal-signal / occurrence of a profiled
            # PID in a kernel message.
            if oom_entry is not None:
                logger.warning("General OOM", oom=json.dumps(oom_entry._asdict()))
                self._disable_profiling(JavaSafemodeOptions.GENERAL_OOM)
            elif signal_entry is not None:
                logger.warning("General signal", signal=json.dumps(signal_entry._asdict()))
                self._disable_profiling(JavaSafemodeOptions.GENERAL_SIGNALED)
            elif any(str(p) in text for p in self._profiled_pids):
                logger.warning("Profiled PID shows in kernel message line", line=text)
                self._disable_profiling(JavaSafemodeOptions.PID_IN_KERNEL_MESSAGES)

    def _handle_new_kernel_messages(self) -> None:
        try:
            messages = list(self._kernel_messages_provider.iter_new_messages())
        except Exception:
            logger.exception("Error iterating new kernel messages")
        else:
            self._handle_kernel_messages(messages)

    def snapshot(self) -> ProcessToProfileData:
        try:
            return super().snapshot()
        finally:
            self._handle_new_kernel_messages()
            self._profiled_pids -= self._pids_to_remove
            self._want_to_profile_pids -= self._pids_to_remove
            for pid in self._pids_to_remove:
                self._pid_to_java_version.pop(pid, None)
            self._pids_to_remove.clear()
