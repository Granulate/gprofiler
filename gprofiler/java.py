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
from typing import Mapping, Optional

import psutil
from psutil import Process

from .merge import parse_collapsed
from .exceptions import StopEventSetException
from .utils import (
    run_process,
    pgrep_exe,
    resource_path,
    resolve_proc_root_links,
    remove_prefix,
    touch_path,
    is_same_ns,
    assert_program_installed,
    TEMPORARY_STORAGE_PATH,
)

logger = logging.getLogger(__name__)


class JavaProfiler:
    FORMAT_PARAMS = "ann,sig"
    OUTPUT_FORMAT = "collapsed"
    JDK_EXCLUSIONS = ["OpenJ9", "Zing"]

    def __init__(self, frequency: int, duration: int, use_itimer: bool, stop_event: Event, storage_dir: str):
        logger.info(f"Initializing Java profiler (frequency: {frequency}hz, duration: {duration}s)")

        # async-profiler accepts interval between samples (nanoseconds)
        self._interval = int((1 / frequency) * 1000_000_000)
        self._duration = duration
        self._use_itimer = use_itimer
        self._stop_event = stop_event
        self._storage_dir = storage_dir

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        pass

    def is_jdk_version_supported(self, java_version_cmd_output: str) -> bool:
        return all(exclusion not in java_version_cmd_output for exclusion in self.JDK_EXCLUSIONS)

    def get_async_profiler_start_cmd(
        self,
        pid: int,
        event_type: str,
        interval: int,
        output_path: str,
        jattach_path: str,
        async_profiler_lib_path: str,
        log_path: str,
    ):
        return [
            jattach_path,
            str(pid),
            "load",
            async_profiler_lib_path,
            "true",
            f"start,event={event_type},file={output_path},{self.OUTPUT_FORMAT},"
            f"{self.FORMAT_PARAMS},interval={interval},framebuf=2000000,log={log_path}",
        ]

    def get_async_profiler_stop_cmd(
        self, pid: int, output_path: str, jattach_path: str, async_profiler_lib_path: str, log_path: str
    ):
        return [
            jattach_path,
            str(pid),
            "load",
            async_profiler_lib_path,
            "true",
            f"stop,file={output_path},{self.OUTPUT_FORMAT},{self.FORMAT_PARAMS},log={log_path}",
        ]

    def run_async_profiler(self, cmd: str, log_path_host: str):
        try:
            run_process(cmd)
        except CalledProcessError:
            if os.path.exists(log_path_host):
                logger.warning(f"async-profiler log: {Path(log_path_host).read_text()}")
            raise

    def profile_process(self, process: Process) -> Optional[Mapping[str, int]]:
        logger.info(f"Profiling java process {process.pid}...")

        assert_program_installed("nsenter")

        # Get Java version
        try:
            java_version_cmd_output = run_process(
                [
                    "nsenter",
                    "-t",
                    str(process.pid),
                    "--mount",
                    "--pid",
                    os.readlink(f"/proc/{process.pid}/exe"),
                    "-version",
                ]
            )
        except CalledProcessError as e:
            raise Exception("Failed to get java version: {}".format(e))

        # Version is printed to stderr
        if not self.is_jdk_version_supported(java_version_cmd_output.stderr.decode()):
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
            os.makedirs(storage_dir_host)
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

        profiler_event = "itimer" if self._use_itimer else "cpu"
        try:
            self.run_async_profiler(
                self.get_async_profiler_start_cmd(
                    process.pid,
                    profiler_event,
                    self._interval,
                    output_path_process,
                    resource_path("java/jattach"),
                    libasyncprofiler_path_process,
                    log_path_process,
                ),
                log_path_host,
            )
        except CalledProcessError:
            is_loaded = f" {libasyncprofiler_path_process}" in Path(f"/proc/{process.pid}/maps").read_text()
            logger.warning(f"async-profiler DSO was{'' if is_loaded else ' not'} loaded into {process.pid}")
            raise

        self._stop_event.wait(self._duration)
        if process.is_running():
            self.run_async_profiler(
                self.get_async_profiler_stop_cmd(
                    process.pid,
                    output_path_process,
                    resource_path("java/jattach"),
                    libasyncprofiler_path_process,
                    log_path_process,
                ),
                log_path_host,
            )

        if self._stop_event.is_set():
            raise StopEventSetException()

        logger.info(f"Finished profiling process {process.pid}")
        return parse_collapsed(Path(output_path_host).read_text())

    def profile_processes(self):
        futures = []
        results = {}
        processes = list(pgrep_exe(r"^.+/java$"))
        if not processes:
            return {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(processes)) as executor:
            for process in processes:
                future = executor.submit(self.profile_process, process)
                future.pid = process.pid
                futures.append(future)

            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    if result is not None:
                        results[future.pid] = result
                except StopEventSetException:
                    raise
                except Exception:
                    logger.exception(f"Failed to profile Java process {future.pid}")

        return results
