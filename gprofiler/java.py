#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import concurrent.futures
import errno
import functools
import logging
import os
import shutil
from pathlib import Path
from threading import Event
from typing import List, Mapping, Optional

import psutil
from psutil import Process

from gprofiler.exceptions import CalledProcessError, StopEventSetException
from gprofiler.merge import parse_one_collapsed
from gprofiler.profiler_base import ProfilerBase
from gprofiler.utils import (
    TEMPORARY_STORAGE_PATH,
    get_process_nspid,
    pgrep_exe,
    remove_path,
    remove_prefix,
    resolve_proc_root_links,
    resource_path,
    run_in_ns,
    run_process,
    touch_path,
)

logger = logging.getLogger(__name__)


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


class AsyncProfiledProcess:
    """
    Represents a process profiled with async-profiler.
    """

    AP_EVENT_TYPE = "itimer"
    FORMAT_PARAMS = "ann,sig"
    OUTPUT_FORMAT = "collapsed"
    OUTPUTS_MODE = 0o622  # readable by root, writable by all

    def __init__(self, process: Process, storage_dir: str):
        self.process = process
        self._process_root = f"/proc/{process.pid}/root"
        # not using storage_dir for AP itself on purpose: this path should remain constant for the lifetime
        # of the target process, so AP is loaded exactly once (if we have multiple paths, AP can be loaded
        # multiple times into the process)
        # without depending on storage_dir here, we maintain the same path even if gProfiler is re-run,
        # because storage_dir changes between runs.
        self._ap_dir = os.path.join(TEMPORARY_STORAGE_PATH, "async-profiler")
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
        # ignore_errors because we are deleting paths via /proc/pid/root - and the process
        # might have gone down already.
        # remove them as best effort.
        remove_path(self._output_path_host, missing_ok=True)
        remove_path(self._log_path_host, missing_ok=True)

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
        shutil.copy(resource_path("java/libasyncProfiler.so"), libap_tmp)
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
        free_disk = psutil.disk_usage(self._ap_dir_host).free
        if free_disk < 250 * 1024:
            raise Exception(f"Not enough free disk space: {free_disk}kb (path: {self._output_path_host}")

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
            f"start,event={self.AP_EVENT_TYPE},file={self._output_path_process},{self.OUTPUT_FORMAT},"
            f"{self.FORMAT_PARAMS},interval={interval},framebuf=2000000,log={self._log_path_process}",
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

    def start_async_profiler(self, interval: int) -> bool:
        """
        Returns True if profiling was started; False if it was already started.
        """
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
            if not self.process.is_running() or not os.path.exists(self._process_root):
                return None
            raise


class JavaProfiler(ProfilerBase):
    JDK_EXCLUSIONS = ["OpenJ9", "Zing"]
    SKIP_VERSION_CHECK_BINARIES = ["jsvc"]

    def __init__(self, frequency: int, duration: int, stop_event: Event, storage_dir: str):
        super().__init__()
        logger.info(f"Initializing Java profiler (frequency: {frequency}hz, duration: {duration}s)")

        # async-profiler accepts interval between samples (nanoseconds)
        self._interval = int((1 / frequency) * 1000_000_000)
        self._duration = duration
        self._stop_event = stop_event
        self._storage_dir = storage_dir

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

    def _profile_process(self, process: Process) -> Optional[Mapping[str, int]]:
        logger.info(f"Profiling java process {process.pid}...")

        # Get Java version
        if os.path.basename(process.exe()) not in self.SKIP_VERSION_CHECK_BINARIES:
            if not self._is_jdk_version_supported(self._get_java_version(process)):
                logger.warning(f"Process {process.pid} running unsupported Java version, skipping...")
                return None

        with AsyncProfiledProcess(process, self._storage_dir) as ap_proc:
            return self._profile_ap_process(ap_proc)

    def _profile_ap_process(self, ap_proc: AsyncProfiledProcess) -> Optional[Mapping[str, int]]:
        started = ap_proc.start_async_profiler(self._interval)
        if not started:
            logger.info(f"Found async-profiler already started on {ap_proc.process.pid}, trying to stop it...")
            # stop, and try to start again. this might happen if AP & gProfiler go out of sync: for example,
            # gProfiler being stopped brutally, while AP keeps running. If gProfiler is later started again, it will
            # try to start AP again...
            ap_proc.stop_async_profiler(with_output=False)
            started = ap_proc.start_async_profiler(self._interval)
            if not started:
                raise Exception(
                    f"async-profiler is still running in {ap_proc.process.pid}, even after trying to stop it!"
                )

        self._stop_event.wait(self._duration)

        if ap_proc.process.is_running():
            ap_proc.stop_async_profiler(True)
        else:
            logger.info(f"Profiled process {ap_proc.process.pid} exited before stopping async-profiler")
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
            return parse_one_collapsed(output)

    def snapshot(self) -> Mapping[int, Mapping[str, int]]:
        processes = list(pgrep_exe(r"^.+/(java|jsvc)$"))
        if not processes:
            return {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(processes)) as executor:
            futures = {}
            for process in processes:
                futures[executor.submit(self._profile_process, process)] = process.pid

            results = {}
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    if result is not None:
                        results[futures[future]] = result
                except StopEventSetException:
                    raise
                except Exception:
                    logger.exception(f"Failed to profile Java process {futures[future]}")

        return results
