#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import errno
import functools
import os
import re
import shutil
from pathlib import Path
from threading import Event
from typing import List, Optional

import psutil
from psutil import Process

from gprofiler.exceptions import CalledProcessError
from gprofiler.gprofiler_types import StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.merge import parse_one_collapsed
from gprofiler.profilers.profiler_base import ProcessProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import (
    TEMPORARY_STORAGE_PATH,
    get_mnt_ns_ancestor,
    get_process_nspid,
    pgrep_maps,
    process_comm,
    read_perf_event_mlock_kb,
    remove_path,
    remove_prefix,
    resolve_proc_root_links,
    resource_path,
    run_in_ns,
    run_process,
    touch_path,
    wait_event,
    write_perf_event_mlock_kb,
)

NATIVE_FRAMES_REGEX = re.compile(r"^Native frames:[^\n]*\n(.*?)\n\n", re.MULTILINE | re.DOTALL)
"""
See VMError::print_native_stack.
Example:
    Native frames: (J=compiled Java code, j=interpreted, Vv=VM code, C=native code)
    C  [libc.so.6+0x18e4e1]
    C  [libasyncProfiler.so+0x1bb4e]  Profiler::dump(std::ostream&, Arguments&)+0xce
    C  [libasyncProfiler.so+0x1bcae]  Profiler::runInternal(Arguments&, std::ostream&)+0x9e
    C  [libasyncProfiler.so+0x1c242]  Profiler::run(Arguments&)+0x212
    C  [libasyncProfiler.so+0x48d81]  Agent_OnAttach+0x1e1
    V  [libjvm.so+0x7ea65b]
    V  [libjvm.so+0x2f5e62]
    V  [libjvm.so+0xb08d2f]
    V  [libjvm.so+0xb0a0fa]
    V  [libjvm.so+0x990552]
    C  [libpthread.so.0+0x76db]  start_thread+0xdb
"""

SIGINFO_REGEX = re.compile(r"^siginfo: ([^\n]*)", re.MULTILINE | re.DOTALL)
"""
See os::print_siginfo
Example:
    siginfo: si_signo: 11 (SIGSEGV), si_code: 0 (SI_USER), si_pid: 537787, si_uid: 0
"""

CONTAINER_INFO_REGEX = re.compile(r"^container \(cgroup\) information:\n(.*?)\n\n", re.MULTILINE | re.DOTALL)
"""
See os::Linux::print_container_info
Example:
    container (cgroup) information:
    container_type: cgroupv1
    cpu_cpuset_cpus: 0-15
    cpu_memory_nodes: 0
    active_processor_count: 16
    cpu_quota: -1
    cpu_period: 100000
    cpu_shares: -1
    memory_limit_in_bytes: -1
    memory_and_swap_limit_in_bytes: -2
    memory_soft_limit_in_bytes: -1
    memory_usage_in_bytes: 26905034752
    memory_max_usage_in_bytes: 27891224576
"""

VM_INFO_REGEX = re.compile(r"^vm_info: ([^\n]*)", re.MULTILINE | re.DOTALL)
"""
This is the last line printed in VMError::report.
Example:
    vm_info: OpenJDK 64-Bit Server VM (25.292-b10) for linux-amd64 JRE (1.8.0_292-8u292-b10-0ubuntu1~18.04-b10), ...
"""

logger = get_logger_adapter(__name__)


class JattachException(CalledProcessError):
    def __init__(self, returncode, cmd, stdout, stderr, target_pid: int, ap_log: str):
        super().__init__(returncode, cmd, stdout, stderr)
        self._target_pid = target_pid
        self._ap_log = ap_log

    def __str__(self):
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

    def __init__(self, process: Process, storage_dir: str, buildids: bool, mode: str, safemode: int):
        self.process = process
        # access the process' root via its topmost parent/ancestor which uses the same mount namespace.
        # this allows us to access the files after the process exits:
        # * for processes that run in host mount NS - their ancestor is always available (it's going to be PID 1)
        # * for processes that run in a container, and the container remains running after they exit - hence, the
        #   ancestor is still alive.
        # there is a hidden assumption here that neither the ancestor nor the process will change their mount
        # namespace. I think it's okay to assume that.
        self._process_root = f"/proc/{get_mnt_ns_ancestor(process)}/root"
        self._cmdline = process.cmdline()
        self._cwd = process.cwd()
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
        self._safemode = safemode

    def __enter__(self):
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

    def __exit__(self, exc_type, exc_val, exc_tb):
        # ignore_errors because we are deleting paths via /proc/pid/root - and the pid
        # we're using might have gone down already.
        # remove them as best effort.
        remove_path(self._output_path_host, missing_ok=True)
        remove_path(self._log_path_host, missing_ok=True)

    def _isfile(self, path):
        if not path.startswith("/"):
            # relative path
            path = f"{self._cwd}/{path}"
        return os.path.isfile(resolve_proc_root_links(self._process_root, path))

    def locate_hotspot_error_file(self) -> Optional[str]:
        default_error_file = f"hs_err_pid{self.process.pid}.log"
        locations = [f"{default_error_file}", f"/tmp/{default_error_file}"]
        for arg in self._cmdline:
            if arg.startswith("-XX:ErrorFile="):
                _, error_file = arg.split("=", maxsplit=1)
                locations.insert(0, error_file.replace("%p", str(self.process.pid)))
                break

        for path in locations:
            if self._isfile(path):
                return path
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

    def _get_start_cmd(self, interval: int) -> List[str]:
        return self._get_base_cmd() + [
            f"start,event={self._mode},file={self._output_path_process},"
            f"{self.OUTPUT_FORMAT},{self.FORMAT_PARAMS},interval={interval},framebuf=2000000,"
            f"log={self._log_path_process}{',buildids' if self._buildids else ''}"
            f"{',fdtransfer' if self._mode == 'cpu' else ''}"
            f",safemode={self._safemode}"
        ]

    def _get_stop_cmd(self, with_output: bool) -> List[str]:
        ap_params = ["stop"]
        if with_output:
            ap_params.append(f"file={self._output_path_process}")
            ap_params.append(self.OUTPUT_FORMAT)
            ap_params.append(self.FORMAT_PARAMS)
        ap_params.append(f"log={self._log_path_process}")
        return self._get_base_cmd() + [",".join(ap_params)]

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
            # check for existence of self._process_root as well, because is_running returns True
            # when the process is a zombie (and /proc/pid/root is not available in that case)
            if not self.process.is_running() or not os.path.exists(f"/proc/{self.process.pid}/root"):
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
            " Defaults to '%(default)s' (which means 'all enabled').",
        ),
    ],
)
class JavaProfiler(ProcessProfilerBase):
    JDK_EXCLUSIONS = ["OpenJ9", "Zing"]

    _new_perf_event_mlock_kb = 8192

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
        java_mode: str,
    ):
        assert java_mode == "ap", "Java profiler should not be initialized, wrong java_mode value given"
        super().__init__(frequency, duration, stop_event, storage_dir)

        # async-profiler accepts interval between samples (nanoseconds)
        self._interval = int((1 / frequency) * 1000_000_000)
        self._buildids = java_async_profiler_buildids
        self._version_check = java_version_check
        self._mode = java_async_profiler_mode
        self._safemode = java_async_profiler_safemode
        self._saved_mlock: Optional[int] = None

    def _is_jdk_version_supported(self, java_version_cmd_output: str) -> bool:
        return all(exclusion not in java_version_cmd_output for exclusion in self.JDK_EXCLUSIONS)

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

        java_version_cmd_output = None

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

    def _profile_process(self, process: Process) -> Optional[StackToSampleCount]:
        logger.info(f"Profiling process {process.pid} with async-profiler")

        # Get Java version
        # TODO we can get the "java" binary by extracting the java home from the libjvm path,
        # then check with that instead (if exe isn't java)
        if self._version_check and os.path.basename(process.exe()) == "java":
            if not self._is_jdk_version_supported(self._get_java_version(process)):
                logger.warning(f"Process {process.pid} running unsupported Java version, skipping...")
                return None

        with AsyncProfiledProcess(process, self._storage_dir, self._buildids, self._mode, self._safemode) as ap_proc:
            return self._profile_ap_process(ap_proc)

    def _profile_ap_process(self, ap_proc: AsyncProfiledProcess) -> Optional[StackToSampleCount]:
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
            wait_event(self._duration, self._stop_event, lambda: not ap_proc.process.is_running(), interval=1)
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
            if ap_proc.process.is_running():
                ap_proc.stop_async_profiler(True)

        output = ap_proc.read_output()
        if output is None:
            logger.warning(
                f"Profiled process {ap_proc.process.pid} exited after stopping async-profiler"
                " but before reading the output"
            )
            return None
        else:
            logger.info(f"Finished profiling process {ap_proc.process.pid}")
            return parse_one_collapsed(output, process_comm(ap_proc.process))

    def _check_hotspot_error(self, ap_proc):
        error_file = ap_proc.locate_hotspot_error_file()
        if not error_file:
            return

        pid = ap_proc.process.pid
        logger.info(f"Found Hotspot error log at {error_file}")
        contents = open(error_file).read()
        m = VM_INFO_REGEX.search(contents)
        if m:
            logger.error(f"Pid {pid} Hotspot VM info: {m[1]}")
        m = SIGINFO_REGEX.search(contents)
        if m:
            logger.error(f"Pid {pid} Hotspot siginfo: {m[1]}")
        m = NATIVE_FRAMES_REGEX.search(contents)
        if m:
            logger.error(f"Pid {pid} Hotspot native frames:\n{m[1]}")
        m = CONTAINER_INFO_REGEX.search(contents)
        if m:
            logger.error(f"Pid {pid} Hotspot container info:\n{m[1]}")

    def _select_processes_to_profile(self) -> List[Process]:
        return pgrep_maps(r"^.+/libjvm\.so$")

    def start(self) -> None:
        super().start()
        if self._mode == "cpu":
            # short tech dive:
            # perf has this accounting logic when mmaping perf_event_open fds (from kernel/events/core.c:perf_mmap())
            # 1. if the *user* locked pages have not exceeded the limit of perf_event_mlock_kb, charge from
            #    that limit.
            # 2. after exceeding, charge from ulimit (mlock)
            # 3. if both are expended, fail the mmap unless task is privileged / perf_event_paranoid is
            #    permissive (-1).
            #
            # in our case, we run "perf" alongside and it starts before async-profiler. so its mmaps get charged from
            # the user (root) locked pages.
            # then, when async-profiler starts, and we profile a Java process running in a container as root (which is
            # common), it is treated as the same user, and since its limit is expended, and the container is not
            # privileged & has low mlock ulimit (Docker defaults to 64) - async-profiler fails to mmap!
            # instead, we update perf_event_mlock_kb here for the lifetime of gProfiler, leaving some room
            # for async-profiler (and also make sure "perf" doesn't use it in its entirety)
            #
            # (alternatively, we could change async-profiler's fdtransfer to do the mmap as well as the perf_event_open;
            # this way, the privileged program gets charged, and when async-profiler mmaps it again, it will use the
            # same pages and won't get charged).
            self._saved_mlock = read_perf_event_mlock_kb()
            write_perf_event_mlock_kb(self._new_perf_event_mlock_kb)

    def stop(self) -> None:
        super().stop()
        if self._saved_mlock is not None:
            write_perf_event_mlock_kb(self._saved_mlock)
