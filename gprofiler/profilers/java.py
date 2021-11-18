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
from packaging.version import Version
from psutil import Process

from gprofiler.exceptions import CalledProcessError, StopEventSetException
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
    write_perf_event_mlock_kb,
)

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


class JvmVersion:
    def __init__(self, version: Version, build: int, name: str):
        self.version = version
        self.build = build
        self.name = name

    def __repr__(self):
        return f"JvmVersion({self.version}, {self.build!r}, {self.name!r})"


# Parse java version information from "java -version" output
def parse_jvm_version(version_string: str) -> JvmVersion:
    # Example java -version output:
    #   openjdk version "1.8.0_265"
    #   OpenJDK Runtime Environment (AdoptOpenJDK)(build 1.8.0_265-b01)
    #   OpenJDK 64-Bit Server VM (AdoptOpenJDK)(build 25.265-b01, mixed mode)
    # We are taking the version from the first line, and the build number and vm name from the last line

    lines = version_string.splitlines()
    # version is always in quotes
    _, version_str, _ = lines[0].split('"')
    build_str = lines[2].split("(build ")[1]
    assert "," in build_str, f"Didn't find comma in build information: {build_str!r}"
    # Extra information we don't care about is placed after a comma
    build_str = build_str[: build_str.find(",")]

    if version_str.endswith("-internal"):
        # Not sure what this means, ignore
        version_str = version_str[: -len("-internal")]

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
            default=127,
            choices=range(0, 128),
            metavar="[0-127]",
            help="Controls the 'safemode' parameter passed to async-profiler. This is parameter denotes multiple"
            " bits that describe different stack recovery techniques which async-profiler uses (see StackRecovery"
            " enum in async-profiler's code, in profiler.cpp)."
            " Defaults to '%(default)s').",
        ),
        ProfilerArgument(
            "--java-safemode", dest="java_safemode", action="store_true", help="Sets the java profiler to a safe mode"
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
        15: (Version("15.0.1"), 9),
        16: (Version("16"), 36),
    }

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
        java_safemode: bool,
        java_mode: str,
    ):
        assert java_mode == "ap", "Java profiler should not be initialized, wrong java_mode value given"
        super().__init__(frequency, duration, stop_event, storage_dir)

        if java_safemode:
            assert java_version_check, "Java version checks are mandatory in --java-safemode"
            assert java_async_profiler_safemode == 127, "Async-profiler safemode must be set to 127 in --java-safemode"

        # async-profiler accepts interval between samples (nanoseconds)
        self._interval = int((1 / frequency) * 1000_000_000)
        self._buildids = java_async_profiler_buildids
        self._version_check = java_version_check
        if not self._version_check:
            logger.warning("Java version checks are disabled")
        self._mode = java_async_profiler_mode
        self._safemode = java_async_profiler_safemode
        if self._safemode:
            logger.debug("Java safemode enabled")
        self._saved_mlock: Optional[int] = None
        self._java_safemode = java_safemode

    def _is_jvm_type_supported(self, java_version_cmd_output: str) -> bool:
        return all(exclusion not in java_version_cmd_output for exclusion in self.JDK_EXCLUSIONS)

    def _is_jvm_version_supported(self, java_version_cmd_output: str) -> bool:
        try:
            jvm_version = parse_jvm_version(java_version_cmd_output)
            logger.info(f"Checking support for java version {jvm_version}")
        except Exception as e:
            logger.error(f"Failed to parse java -version output {java_version_cmd_output}: {e}")
            return False

        if jvm_version.version.major not in self.MINIMAL_SUPPORTED_VERSIONS:
            logger.error(f"Unsupported java version {jvm_version.version}")
            return False
        min_version, min_build = self.MINIMAL_SUPPORTED_VERSIONS[jvm_version.version.major]
        if jvm_version.version < min_version:
            logger.error(f"Unsupported java version {jvm_version.version}")
            return False
        elif jvm_version.version == min_version:
            if jvm_version.build < min_build:
                logger.error(f"Unsupported build number {jvm_version.build} for java version {jvm_version.version}")
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

    def _check_jvm_type_supported(self, process: Process, java_version_output: str) -> bool:
        if not self._is_jvm_type_supported(java_version_output):
            logger.error(f"Process {process.pid} running unsupported JVM ({java_version_output!r}), skipping...")
            return False

        return True

    def _is_profiling_supported(self, process: Process) -> bool:
        process_basename = os.path.basename(process.exe())
        if self._java_safemode:
            # TODO we can get the "java" binary by extracting the java home from the libjvm path,
            # then check with that instead (if exe isn't java)
            if process_basename != "java":
                logger.error(
                    f"Non-java basenamed process {process.pid} ({process.exe()!r}), skipping..."
                    " (disable --java-safemode to profile it anyway)"
                )
                return False

            java_version_output = self._get_java_version(process)

            if not self._check_jvm_type_supported(process, java_version_output):
                return False

            if not self._is_jvm_version_supported(java_version_output):
                logger.error(
                    f"Process {process.pid} running unsupported Java version ({java_version_output!r}), skipping..."
                    " (disable --java-safemode to profile it anyway)"
                )
                return False
        else:
            if self._version_check and process_basename == "java":
                java_version_output = self._get_java_version(process)
                if self._check_jvm_type_supported(process, java_version_output):
                    return False

        return True

    def _profile_process(self, process: Process) -> Optional[StackToSampleCount]:
        if not self._is_profiling_supported(process):
            return None

        logger.info(f"Profiling process {process.pid} with async-profiler")
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

        self._stop_event.wait(self._duration)

        if ap_proc.process.is_running():
            ap_proc.stop_async_profiler(True)
        else:
            logger.debug(f"Profiled process {ap_proc.process.pid} exited before stopping async-profiler")
            # no output in this case :/
            return None

        if self._stop_event.is_set():
            raise StopEventSetException()

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
