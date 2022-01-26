#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import errno
import functools
import json
import os
import re
import shutil
from collections import Counter
from enum import Enum
from itertools import dropwhile
from pathlib import Path
from subprocess import CompletedProcess
from threading import Event
from types import TracebackType
from typing import List, Optional, Set, Any, cast, Type

import psutil
from granulate_utils.java import (
    CONTAINER_INFO_REGEX,
    NATIVE_FRAMES_REGEX,
    SIGINFO_REGEX,
    VM_INFO_REGEX,
    is_java_fatal_signal,
    java_exit_code_to_signo,
    locate_hotspot_error_file,
)
from granulate_utils.linux import proc_events
from granulate_utils.linux.kernel_messages import KernelMessage
from granulate_utils.linux.ns import get_proc_root_path, resolve_proc_root_links, run_in_ns
from granulate_utils.linux.oom import get_oom_entry
from granulate_utils.linux.signals import get_signal_entry
from packaging.version import Version
from psutil import Process

from gprofiler.exceptions import CalledProcessError
from gprofiler.gprofiler_types import ProcessToStackSampleCounters, StackToSampleCount
from gprofiler.kernel_messages import get_kernel_messages_provider
from gprofiler.log import get_logger_adapter
from gprofiler.merge import parse_one_collapsed
from gprofiler.profilers.profiler_base import ProcessProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import (
    TEMPORARY_STORAGE_PATH,
    get_process_nspid,
    is_process_running,
    pgrep_maps,
    process_comm,
    remove_path,
    remove_prefix,
    resource_path,
    run_process,
    touch_path,
    wait_event,
)

logger = get_logger_adapter(__name__)


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


class JattachException(CalledProcessError):
    def __init__(self, returncode: int, cmd: Any, stdout: Any, stderr: Any, target_pid: int, ap_log: str):
        super().__init__(returncode, cmd, stdout, stderr)
        self._target_pid = target_pid
        self._ap_log = ap_log

    def __str__(self) -> str:
        ap_log = self._ap_log.strip()
        if not ap_log:
            ap_log = "(empty)"
        return super().__str__() + f"\nJava PID: {self._target_pid}\nasync-profiler log:\n{ap_log}"

    def get_ap_log(self) -> str:
        return self._ap_log


@functools.lru_cache(maxsize=1)
def jattach_path() -> str:
    return resource_path("java/jattach")


@functools.lru_cache(maxsize=1)
def fdtransfer_path() -> str:
    return resource_path("java/fdtransfer")


@functools.lru_cache(maxsize=1)
def get_ap_version() -> str:
    return Path(resource_path("java/async-profiler-version")).read_text()


class AsyncProfiledProcess:
    """
    Represents a process profiled with async-profiler.
    """

    FORMAT_PARAMS = "ann,sig"
    OUTPUT_FORMAT = "collapsed"
    OUTPUTS_MODE = 0o622  # readable by root, writable by all

    def __init__(
        self,
        process: Process,
        storage_dir: str,
        buildids: bool,
        mode: str,
        ap_safemode: int,
        ap_args: str,
    ):
        self.process = process
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
        self._ap_dir = os.path.join(
            TEMPORARY_STORAGE_PATH,
            f"async-profiler-{get_ap_version()}",
            "musl" if self._is_musl() else "glibc",
        )
        self._ap_dir_host = resolve_proc_root_links(self._process_root, self._ap_dir)

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
        self._ap_safemode = ap_safemode
        self._ap_args = ap_args

    def __enter__(self) -> "AsyncProfiledProcess":
        os.makedirs(self._ap_dir_host, 0o755, exist_ok=True)
        os.makedirs(self._storage_dir_host, 0o755, exist_ok=True)

        self._check_disk_requirements()

        # make out & log paths writable for all, so target process can write to them.
        # see comment on TemporaryDirectoryWithMode in GProfiler.__init__.
        touch_path(self._output_path_host, self.OUTPUTS_MODE)
        self._recreate_log()
        # copy libasyncProfiler.so if needed
        if not os.path.exists(self._libap_path_host):
            self._copy_libap()

        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                 exc_ctb: Optional[TracebackType]) -> None:
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
        # nspid is required
        if self._nspid is None:
            # TODO: fix get_process_nspid so it always succeeds
            return None

        for path in locate_hotspot_error_file(self._nspid, self._cmdline):
            realpath = self._existing_realpath(path)
            if realpath is not None:
                return realpath
        return None

    @functools.lru_cache(maxsize=1)
    def _is_musl(self) -> bool:
        # Is target process musl-based?
        return any("ld-musl" in m.path for m in self.process.memory_maps())

    def _copy_libap(self) -> None:
        # copy *is* racy with respect to other processes running in the same namespace, because they all use
        # the same directory for libasyncProfiler.so, as we don't want to create too many copies of it that
        # will waste disk space.
        # my attempt here is to produce a race-free way of ensuring libasyncProfiler.so was fully copied
        # to a *single* path, per namespace.
        # newer kernels (>3.15 for ext4) have the renameat2 syscall, with RENAME_NOREPLACE, which lets you
        # atomically move a file without replacing if the target already exists.
        # this function operates similarly on directories. if you rename(dir_a, dir_b) then dir_b is replaced
        # with dir_a iff it's empty. this gives us the same semantics.
        # so, we create a temporary directory on the same filesystem (so move is atomic) in a race-free way
        # by including the PID in its name; then we move it.
        # TODO: if we ever move away from the multithreaded model, we can get rid of this complexity
        # by ensuring a known order of execution.
        ap_dir_host_tmp = f"{self._ap_dir_host}.{self.process.pid}"
        os.makedirs(ap_dir_host_tmp)
        libap_tmp = os.path.join(ap_dir_host_tmp, "libasyncProfiler.so")
        shutil.copy(
            resource_path(os.path.join("java", "musl" if self._is_musl() else "glibc", "libasyncProfiler.so")),
            libap_tmp,
        )
        os.chmod(libap_tmp, 0o755)  # make it accessible for all; needed with PyInstaller, which extracts files as 0700
        try:
            os.rename(ap_dir_host_tmp, self._ap_dir_host)
        except OSError as e:
            if e.errno == errno.ENOTEMPTY:
                # remove our copy
                shutil.rmtree(ap_dir_host_tmp)
                # it should have been created by somene else.
                assert os.path.exists(self._libap_path_host)
            else:
                raise

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

    def _get_start_cmd(self, interval: int) -> List[str]:
        return self._get_base_cmd() + [
            f"start,event={self._mode},file={self._output_path_process},"
            f"{self.OUTPUT_FORMAT},{self.FORMAT_PARAMS},interval={interval},"
            f"log={self._log_path_process}{',buildids' if self._buildids else ''}"
            f"{',fdtransfer' if self._mode == 'cpu' else ''}"
            f",safemode={self._ap_safemode}{self._get_extra_ap_args()}"
        ]

    def _get_stop_cmd(self, with_output: bool) -> List[str]:
        ap_params = ["stop"]
        if with_output:
            ap_params.append(f"file={self._output_path_process}")
            ap_params.append(self.OUTPUT_FORMAT)
            ap_params.append(self.FORMAT_PARAMS)
        ap_params.append(f"log={self._log_path_process}")
        return self._get_base_cmd() + [",".join(ap_params) + self._get_extra_ap_args()]

    def _run_async_profiler(self, cmd: List[str]) -> None:
        try:
            run_process(cmd)
        except CalledProcessError as e:
            if os.path.exists(self._log_path_host):
                log = Path(self._log_path_host)
                ap_log = log.read_text()
                # clean immediately so we don't mix log messages from multiple invocations.
                # this is also what AP's profiler.sh does.
                log.unlink()
                self._recreate_log()
            else:
                ap_log = "(log file doesn't exist)"

            raise JattachException(e.returncode, e.cmd, e.stdout, e.stderr, self.process.pid, ap_log) from None

    def _run_fdtransfer(self) -> None:
        """
        Start fdtransfer; it will fork & exit once ready, so we can continue with jattach.
        """
        run_process([fdtransfer_path(), str(self.process.pid)], communicate=False)

    def start_async_profiler(self, interval: int, second_try: bool = False) -> bool:
        """
        Returns True if profiling was started; False if it was already started.
        """
        if self._mode == "cpu" and not second_try:
            self._run_fdtransfer()

        start_cmd = self._get_start_cmd(interval)
        try:
            self._run_async_profiler(start_cmd)
            return True
        except JattachException as e:
            is_loaded = f" {self._libap_path_process}\n" in Path(f"/proc/{self.process.pid}/maps").read_text()
            if is_loaded:
                if (
                    e.returncode == 200  # 200 == AP's COMMAND_ERROR
                    and e.get_ap_log() == "[ERROR] Profiler already started\n"
                ):
                    # profiler was already running
                    return False

            logger.warning(f"async-profiler DSO was{'' if is_loaded else ' not'} loaded into {self.process.pid}")
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


class JvmVersion:
    def __init__(self, version: Version, build: int, name: str):
        self.version = version
        self.build = build
        self.name = name

    def __repr__(self) -> str:
        return f"JvmVersion({self.version}, {self.build!r}, {self.name!r})"


# Parse java version information from "java -version" output
def parse_jvm_version(version_string: str) -> JvmVersion:
    # Example java -version output:
    #   openjdk version "1.8.0_265"
    #   OpenJDK Runtime Environment (AdoptOpenJDK)(build 1.8.0_265-b01)
    #   OpenJDK 64-Bit Server VM (AdoptOpenJDK)(build 25.265-b01, mixed mode)
    # We are taking the version from the first line, and the build number and vm name from the last line

    lines = version_string.splitlines()

    # the version always starts with "openjdk version" or "java version". strip all lines
    # before that.
    lines = list(dropwhile(lambda l: not ("openjdk version" in l or "java version" in l), lines))

    # version is always in quotes
    _, version_str, _ = lines[0].split('"')
    build_str = lines[2].split("(build ")[1]
    assert "," in build_str, f"Didn't find comma in build information: {build_str!r}"
    # Extra information we don't care about is placed after a comma
    build_str = build_str[: build_str.find(",")]

    if version_str.endswith("-internal") or version_str.endswith("-ea"):
        # strip the "internal" or "early access" suffixes
        version_str = version_str.rsplit("-")[0]

    version_list = version_str.split(".")
    if version_list[0] == "1":
        # For java 8 and prior, versioning looks like
        # 1.<major>.0_<minor>-b<build_number>
        # For example 1.8.0_242-b12 means 8.242 with build number 12
        assert len(version_list) == 3, f"Unexpected number of elements for old-style java version: {version_list!r}"
        assert "_" in version_list[-1], f"Did not find expected underscore in old-style java version: {version_list!r}"
        major = version_list[1]
        minor = version_list[-1].split("_")[-1]
        version = Version(f"{major}.{minor}")
        assert (
            build_str[-4:-2] == "-b"
        ), f"Did not find expected build number prefix in old-style java version: {build_str!r}"
        build = int(build_str[-2:])
    else:
        # Since java 9 versioning became more normal, and looks like
        # <version>+<build_number>
        # For example, 11.0.11+9
        version = Version(version_str)
        assert "+" in build_str, f"Did not find expected build number prefix in new-style java version: {build_str!r}"
        # The goal of the regex here is to read the build number until a non-digit character is encountered,
        # since additional information can be appended after it, such as the platform name
        matched = re.match(r"\d+", build_str[build_str.find("+") + 1 :])
        assert matched, f"Unexpected build number format in new-style java version: {build_str!r}"
        build = int(matched[0])

    # There is no real format here, just use the entire description string
    vm_name = lines[2].split("(build")[0].strip()
    return JvmVersion(version, build, vm_name)


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
            help="Skip the JDK version check (that is done before invoking async-profiler)",
        ),
        ProfilerArgument(
            "--java-async-profiler-mode",
            dest="java_async_profiler_mode",
            choices=["cpu", "itimer"],
            default="itimer",
            help="Select async-profiler's mode: 'cpu' (based on perf_events & fdtransfer) or 'itimer' (no perf_events)."
            " Defaults to '%(default)s'.",
        ),
        ProfilerArgument(
            "--java-async-profiler-safemode",
            dest="java_async_profiler_safemode",
            type=int,
            default=0,
            choices=range(0, 128),
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
    ],
)
class JavaProfiler(ProcessProfilerBase):
    JDK_EXCLUSIONS = ["OpenJ9", "Zing"]
    # Major -> (min version, min build number of version)
    MINIMAL_SUPPORTED_VERSIONS = {
        7: (Version("7.76"), 4),
        8: (Version("8.72"), 15),
        11: (Version("11.0.2"), 7),
        12: (Version("12.0.1"), 12),
        14: (Version("14"), 33),
        15: (Version("15.0.1"), 9),
        16: (Version("16"), 36),
        17: (Version("17.0.1"), 12),
    }

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Event,
        storage_dir: str,
        java_async_profiler_buildids: bool,
        java_version_check: bool,
        java_async_profiler_mode: str,
        java_async_profiler_safemode: int,
        java_async_profiler_args: str,
        java_safemode: str,
        java_mode: str,
    ):
        assert java_mode == "ap", "Java profiler should not be initialized, wrong java_mode value given"
        super().__init__(frequency, duration, stop_event, storage_dir)

        # async-profiler accepts interval between samples (nanoseconds)
        self._interval = int((1 / frequency) * 1000_000_000)
        self._buildids = java_async_profiler_buildids
        # simple version check, and
        self._simple_version_check = java_version_check
        if not self._simple_version_check:
            logger.warning("Java version checks are disabled")
        self._mode = java_async_profiler_mode
        self._ap_safemode = java_async_profiler_safemode
        self._ap_args = java_async_profiler_args
        self._init_java_safemode(java_safemode)
        self._should_profile = True
        # if set, profiling is disabled due to this safemode reason.
        self._safemode_disable_reason: Optional[str] = None
        self._profiled_pids: Set[int] = set()
        self._pids_to_remove: Set[int] = set()
        self._kernel_messages_provider = get_kernel_messages_provider()
        self._enabled_proc_events = False

    def _init_java_safemode(self, java_safemode: str) -> None:
        if java_safemode == JAVA_SAFEMODE_ALL:
            self._java_safemode = JAVA_SAFEMODE_ALL_OPTIONS
        else:
            self._java_safemode = java_safemode.split(",") if java_safemode else []

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

        if java_safemode == JAVA_SAFEMODE_ALL:
            assert (
                self._ap_safemode == 127
            ), f"async-profiler safemode must be set to 127 in --java-safemode={JAVA_SAFEMODE_ALL} (or --java-safemode)"

    def _disable_profiling(self, cause: str) -> None:
        if self._safemode_disable_reason is None and cause in self._java_safemode:
            logger.warning("Java profiling has been disabled, will avoid profiling any new java processes", cause=cause)
            self._safemode_disable_reason = cause

    def _profiling_skipped_stack(self, reason: str, comm: str) -> StackToSampleCount:
        # return 1 sample, it will be scaled later in merge_profiles().
        # if --perf-mode=none mode is used, it will not, but we don't have anything logical to
        # do here in that case :/
        return Counter({f"{comm};[Profiling skipped: {reason}]": 1})

    def _is_jvm_type_supported(self, java_version_cmd_output: str) -> bool:
        return all(exclusion not in java_version_cmd_output for exclusion in self.JDK_EXCLUSIONS)

    def _is_jvm_version_supported(self, java_version_cmd_output: str) -> bool:
        try:
            jvm_version = parse_jvm_version(java_version_cmd_output)
            logger.info("Checking support for java version", jvm_version=jvm_version)
        except Exception:
            logger.exception("Failed to parse java -version output", java_version_cmd_output=java_version_cmd_output)
            return False

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

    @staticmethod
    def _get_java_version(process: Process) -> str:
        nspid = get_process_nspid(process.pid)
        if nspid is not None:
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
        else:
            # TODO fix get_process_nspid() for all cases.
            java_path = os.readlink(f"/proc/{process.pid}/exe")

        java_version_cmd_output: Optional[CompletedProcess[bytes]] = None

        def _run_java_version() -> None:
            nonlocal java_version_cmd_output

            java_version_cmd_output = run_process(
                [
                    java_path,
                    "-version",
                ]
            )

        # doesn't work without changing PID NS as well (I'm getting ENOENT for libjli.so)
        run_in_ns(["pid", "mnt"], _run_java_version, process.pid)

        if java_version_cmd_output is None:
            raise Exception("Failed to get java version")

        # Version is printed to stderr
        return java_version_cmd_output.stderr.decode()

    def _check_jvm_type_supported(self, process: Process, java_version_output: str) -> bool:
        if not self._is_jvm_type_supported(java_version_output):
            logger.warning("Unsupported JVM type", java_version_output=java_version_output)
            return False

        return True

    def _is_jvm_profiling_supported(self, process: Process) -> bool:
        process_basename = os.path.basename(process.exe())
        if JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS in self._java_safemode:
            # TODO we can get the "java" binary by extracting the java home from the libjvm path,
            # then check with that instead (if exe isn't java)
            if process_basename != "java":
                logger.warning(
                    "Non-java basenamed process, skipping... (disable "
                    f" --java-safemode={JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS} to profile it anyway)",
                    pid=process.pid,
                    exe=process.exe(),
                )
                return False

            java_version_output = self._get_java_version(process)

            if not self._check_jvm_type_supported(process, java_version_output):
                return False

            if not self._is_jvm_version_supported(java_version_output):
                logger.warning(
                    "Process running unsupported Java version, skipping..."
                    f" (disable --java-safemode={JavaSafemodeOptions.JAVA_EXTENDED_VERSION_CHECKS}"
                    " to profile it anyway)",
                    pid=process.pid,
                    java_version_output=java_version_output,
                )
                return False
        else:
            if self._simple_version_check and process_basename == "java":
                java_version_output = self._get_java_version(process)
                if not self._check_jvm_type_supported(process, java_version_output):
                    return False

        return True

    def _check_async_profiler_loaded(self, process: Process) -> bool:
        if JavaSafemodeOptions.AP_LOADED_CHECK not in self._java_safemode:
            # don't care
            return False

        for mmap in process.memory_maps():
            if "libasyncProfiler.so" in mmap.path and not mmap.path.startswith(TEMPORARY_STORAGE_PATH):
                logger.warning(
                    "Non-gProfiler async-profiler is already loaded to the target process."
                    f" Disable --java-safemode={JavaSafemodeOptions.AP_LOADED_CHECK} to bypass this check.",
                    pid=process.pid,
                    ap_path=mmap.path,
                )
                return True

        return False

    def _profile_process(self, process: Process) -> Optional[StackToSampleCount]:
        comm = process_comm(process)

        if self._safemode_disable_reason is not None:
            return self._profiling_skipped_stack(f"disabled due to {self._safemode_disable_reason}", comm)

        if not self._is_jvm_profiling_supported(process):
            return self._profiling_skipped_stack("profiling this JVM is not supported", comm)

        if self._check_async_profiler_loaded(process):
            return self._profiling_skipped_stack("async-profiler is already loaded", comm)

        # track profiled PIDs only if proc_events are in use, otherwise there is no use in them.
        # TODO: it is possible to run in contexts where we're unable to use proc_events but are able to listen
        # on kernel messages. we can add another mechanism to track PIDs (such as, prune PIDs which have exited)
        # then use the kernel messages listener without proc_events.
        if self._enabled_proc_events:
            self._profiled_pids.add(process.pid)

        logger.info(f"Profiling process {process.pid} with async-profiler")
        with AsyncProfiledProcess(
            process, self._storage_dir, self._buildids, self._mode, self._ap_safemode, self._ap_args
        ) as ap_proc:
            return self._profile_ap_process(ap_proc, comm)

    def _profile_ap_process(self, ap_proc: AsyncProfiledProcess, comm: str) -> Optional[StackToSampleCount]:
        started = ap_proc.start_async_profiler(self._interval)
        if not started:
            logger.info(f"Found async-profiler already started on {ap_proc.process.pid}, trying to stop it...")
            # stop, and try to start again. this might happen if AP & gProfiler go out of sync: for example,
            # gProfiler being stopped brutally, while AP keeps running. If gProfiler is later started again, it will
            # try to start AP again...
            # not using the "resume" action because I'm not sure it properly reconfigures all settings; while stop;start
            # surely does.
            ap_proc.stop_async_profiler(with_output=False)
            started = ap_proc.start_async_profiler(self._interval, second_try=True)
            if not started:
                raise Exception(
                    f"async-profiler is still running in {ap_proc.process.pid}, even after trying to stop it!"
                )

        try:
            wait_event(self._duration, self._stop_event, lambda: not is_process_running(ap_proc.process), interval=1)
        except TimeoutError:
            # Process still running. We will stop the profiler in finally block.
            pass
        else:
            # Process terminated, was it due to an error?
            self._check_hotspot_error(ap_proc)
            logger.debug(f"Profiled process {ap_proc.process.pid} exited before stopping async-profiler")
            # no output in this case :/
            return None
        finally:
            if is_process_running(ap_proc.process):
                ap_proc.stop_async_profiler(True)

        output = ap_proc.read_output()
        if output is None:
            logger.warning(f"Profiled process {ap_proc.process.pid} exited before reading the output")
            return None
        else:
            logger.info(f"Finished profiling process {ap_proc.process.pid}")
            return parse_one_collapsed(output, comm)

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
        return pgrep_maps(r"^.+/libjvm\.so$")

    def start(self) -> None:
        super().start()
        try:
            # needs to run in init net NS - see netlink_kernel_create() call on init_net in cn_init().
            run_in_ns(["net"], lambda: proc_events.register_exit_callback(self._proc_exit_callback), 1)  # type: ignore
        except Exception:
            logger.warning("Failed to enable proc_events listener for exited Java processes", exc_info=True)
        else:
            self._enabled_proc_events = True

    def stop(self) -> None:
        if self._enabled_proc_events:
            proc_events.unregister_exit_callback(self._proc_exit_callback)
            self._enabled_proc_events = False
        super().stop()

    def _proc_exit_callback(self, tid: int, pid: int, exit_code: int) -> None:
        # Notice that we only check the exit code of the main thread here.
        # It's assumed that an error in any of the Java threads will be reflected in the exit code of the main thread.
        if tid in self._profiled_pids:
            self._pids_to_remove.add(tid)

            signo = java_exit_code_to_signo(exit_code)
            if signo is None:
                # not a signal, do not report
                return

            logger.warning("async-profiled Java process exited with signal", pid=tid, signal=signo)

            if is_java_fatal_signal(signo):
                self._disable_profiling(JavaSafemodeOptions.PROFILED_SIGNALED)

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

    def snapshot(self) -> ProcessToStackSampleCounters:
        try:
            return super().snapshot()
        finally:
            self._handle_new_kernel_messages()
            self._profiled_pids -= self._pids_to_remove
            self._pids_to_remove.clear()
