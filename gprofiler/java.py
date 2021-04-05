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
from tempfile import NamedTemporaryFile, mktemp
from threading import Event
from typing import List

import psutil
from psutil import Process

from .merge import parse_collapsed
from .exceptions import StopEventSetException
from .utils import run_process, pgrep_exe, get_self_container_id, resource_path

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

        self._temp_dirs: List[str] = []
        self._self_container_id = get_self_container_id()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        for temp_dir in self._temp_dirs:
            shutil.rmtree(temp_dir)

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

    def profile_process(self, process: Process):
        logger.info(f"Profiling java process {process.pid}...")

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
            return

        process_root = f"/proc/{process.pid}/root"
        storage_dir = process_root + self._storage_dir
        if not os.path.isdir(storage_dir):
            os.makedirs(storage_dir)
            self._temp_dirs.append(storage_dir)
        output_path = NamedTemporaryFile(dir=storage_dir, delete=False).name
        remote_context_output_path = os.path.join(self._storage_dir, os.path.basename(output_path))
        libasyncprofiler_path = os.path.join(self._storage_dir, "libasyncProfiler.so")
        remote_context_libasyncprofiler_path = os.path.join(storage_dir, "libasyncProfiler.so")
        if not os.path.exists(remote_context_libasyncprofiler_path):
            shutil.copy(resource_path("java/libasyncProfiler.so"), remote_context_libasyncprofiler_path)
        log_path = os.path.join(self._storage_dir, os.path.basename(mktemp()))
        log_path_host = process_root + log_path

        os.chmod(output_path, 0o666)

        free_disk = psutil.disk_usage(output_path).free
        if free_disk < 250 * 1024:
            raise Exception(f"Not enough free disk space: {free_disk}kb")

        profiler_event = "itimer" if self._use_itimer else "cpu"
        try:
            self.run_async_profiler(
                self.get_async_profiler_start_cmd(
                    process.pid,
                    profiler_event,
                    self._interval,
                    remote_context_output_path,
                    resource_path("java/jattach"),
                    libasyncprofiler_path,
                    log_path,
                ),
                log_path_host,
            )
        except CalledProcessError:
            is_loaded = f" {libasyncprofiler_path}\n" in Path(f"/proc/{process.pid}/maps").read_text()
            logger.warning(f"async-profiler DSO was{'' if is_loaded else ' not'} loaded into {process.pid}")
            raise

        self._stop_event.wait(self._duration)
        if process.is_running():
            self.run_async_profiler(
                self.get_async_profiler_stop_cmd(
                    process.pid,
                    remote_context_output_path,
                    resource_path("java/jattach"),
                    libasyncprofiler_path,
                    log_path,
                ),
                log_path_host,
            )

        if self._stop_event.is_set():
            raise StopEventSetException()

        logger.info(f"Finished profiling process {process.pid}")
        return parse_collapsed(Path(output_path).read_text())

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
