#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import concurrent.futures
import logging
import os
import shutil
from pathlib import Path
from subprocess import CalledProcessError
from threading import Event
from typing import List, Mapping, Optional

import psutil
from psutil import Process

from gprofiler.exceptions import StopEventSetException
from gprofiler.merge import parse_one_collapsed
from gprofiler.profiler_base import ProfilerBase
from gprofiler.utils import (
    TEMPORARY_STORAGE_PATH,
    get_process_nspid,
    is_same_ns,
    pgrep_exe,
    remove_prefix,
    resolve_proc_root_links,
    resource_path,
    run_in_ns,
    run_process,
    touch_path,
)

logger = logging.getLogger(__name__)


class JavaProfiler(ProfilerBase):
    FORMAT_PARAMS = "ann,sig"
    OUTPUT_FORMAT = "collapsed"
    JDK_EXCLUSIONS = ["OpenJ9", "Zing"]
    SKIP_VERSION_CHECK_BINARIES = ["jsvc"]

    def __init__(self, frequency: int, duration: int, use_itimer: bool, stop_event: Event, storage_dir: str):
        super().__init__()
        logger.info(f"Initializing Java profiler (frequency: {frequency}hz, duration: {duration}s)")

        # async-profiler accepts interval between samples (nanoseconds)
        self._interval = int((1 / frequency) * 1000_000_000)
        self._duration = duration
        self._use_itimer = use_itimer
        self._stop_event = stop_event
        self._storage_dir = storage_dir

    def _is_jdk_version_supported(self, java_version_cmd_output: str) -> bool:
        return all(exclusion not in java_version_cmd_output for exclusion in self.JDK_EXCLUSIONS)

    def _get_async_profiler_start_cmd(
        self,
        pid: int,
        event_type: str,
        interval: int,
        output_path: str,
        jattach_path: str,
        async_profiler_lib_path: str,
        log_path: str,
    ) -> List[str]:
        return [
            jattach_path,
            str(pid),
            "load",
            async_profiler_lib_path,
            "true",
            f"start,event={event_type},file={output_path},{self.OUTPUT_FORMAT},"
            f"{self.FORMAT_PARAMS},interval={interval},framebuf=2000000,log={log_path}",
        ]

    def _get_async_profiler_stop_cmd(
        self, pid: int, output_path: Optional[str], jattach_path: str, async_profiler_lib_path: str, log_path: str
    ) -> List[str]:
        ap_params = ["stop"]
        if output_path is not None:
            ap_params.append(f"file={output_path}")
            ap_params.append(self.OUTPUT_FORMAT)
            ap_params.append(self.FORMAT_PARAMS)
        ap_params.append(f"log={log_path}")
        return [jattach_path, str(pid), "load", async_profiler_lib_path, "true", ",".join(ap_params)]

    def _run_async_profiler(self, cmd: List[str], log_path_host: str, pid: int) -> None:
        try:
            run_process(cmd)
        except CalledProcessError:
            if os.path.exists(log_path_host):
                logger.warning(f"async-profiler (on {pid}) log: {Path(log_path_host).read_text()}")
                os.unlink(log_path_host)
            raise

    def _start_async_profiler(
        self,
        process: Process,
        libasyncprofiler_path_process: str,
        output_path_process: str,
        log_path_host: str,
        log_path_process: str,
    ) -> bool:
        """
        Returns True if profiling was started; False if it was already started.
        """
        profiler_event = "itimer" if self._use_itimer else "cpu"

        try:
            self._run_async_profiler(
                self._get_async_profiler_start_cmd(
                    process.pid,
                    profiler_event,
                    self._interval,
                    output_path_process,
                    resource_path("java/jattach"),
                    libasyncprofiler_path_process,
                    log_path_process,
                ),
                log_path_host,
                process.pid,
            )
            return True
        except CalledProcessError as e:
            is_loaded = f" {libasyncprofiler_path_process}" in Path(f"/proc/{process.pid}/maps").read_text()
            if is_loaded:
                if (
                    e.returncode == 200
                    and e.stdout.decode() == "[ERROR] Profiler already started\n"  # AP's COMMAND_ERROR
                ):

                    return False

            logger.warning(f"async-profiler DSO was{'' if is_loaded else ' not'} loaded into {process.pid}")
            raise

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

        process_root = f"/proc/{process.pid}/root"
        if is_same_ns(process.pid, "mnt"):
            # processes running in my namespace can use my (temporary) storage dir
            tmp_dir = self._storage_dir
        else:
            # processes running in other namespaces will use the base path
            tmp_dir = TEMPORARY_STORAGE_PATH

        # we'll use separated storage directories per process: since multiple processes may run in the
        # same namespace, one may accidentally delete the storage directory of another.
        storage_dir_host = resolve_proc_root_links(process_root, os.path.join(tmp_dir, str(process.pid)))

        try:
            # make it readable & exectuable by all.
            # see comment on TemporaryDirectoryWithMode in GProfiler.__init__.
            os.makedirs(storage_dir_host, 0o755)
            return self._profile_process_with_dir(process, storage_dir_host, process_root)
        finally:
            # ignore_errors because we are deleting paths via /proc/pid/root - and those processes
            # might have went down already.
            shutil.rmtree(storage_dir_host, ignore_errors=True)

    def _profile_process_with_dir(
        self, process: Process, storage_dir_host: str, process_root: str
    ) -> Optional[Mapping[str, int]]:
        output_path_host = os.path.join(storage_dir_host, f"async-profiler-{process.pid}.output")
        touch_path(output_path_host, 0o666)  # make it writable for all, so target process can write
        output_path_process = remove_prefix(output_path_host, process_root)

        libasyncprofiler_path_host = os.path.join(storage_dir_host, "libasyncProfiler.so")
        libasyncprofiler_path_process = remove_prefix(libasyncprofiler_path_host, process_root)
        if not os.path.exists(libasyncprofiler_path_host):
            shutil.copy(resource_path("java/libasyncProfiler.so"), libasyncprofiler_path_host)
            # explicitly chmod to allow access for non-root users
            os.chmod(libasyncprofiler_path_host, 0o755)

        log_path_host = os.path.join(storage_dir_host, f"async-profiler-{process.pid}.log")
        touch_path(log_path_host, 0o666)  # make it writable for all, so target process can write
        log_path_process = remove_prefix(log_path_host, process_root)

        free_disk = psutil.disk_usage(output_path_host).free
        if free_disk < 250 * 1024:
            raise Exception(f"Not enough free disk space: {free_disk}kb")

        self._start_async_profiler(
            process, log_path_process, log_path_host, output_path_process, libasyncprofiler_path_process
        )

        self._stop_event.wait(self._duration)
        if process.is_running():
            self._run_async_profiler(
                self._get_async_profiler_stop_cmd(
                    process.pid,
                    output_path_process,
                    resource_path("java/jattach"),
                    libasyncprofiler_path_process,
                    log_path_process,
                ),
                log_path_host,
                process.pid,
            )
        else:
            # no output in this case :/
            return None

        if self._stop_event.is_set():
            raise StopEventSetException()

        logger.info(f"Finished profiling process {process.pid}")

        try:
            output = Path(output_path_host).read_text()
        except FileNotFoundError:
            # check for existence of process_root as well, because is_running returns True
            # when the process is a zombie (and /proc/pid/root is not available in that case)
            if not process.is_running() or not os.path.exists(process_root):
                return None
            raise
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
