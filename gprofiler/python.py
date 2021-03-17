#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import concurrent.futures
import logging
import os
from pathlib import Path
from threading import Event
from typing import Dict

from .merge import parse_collapsed
from .exceptions import StopEventSetException, ProcessStoppedException
from .utils import pgrep_exe, run_process, resource_path

logger = logging.getLogger(__name__)

PYTHON_PROFILER_MAX_FREQUENCY = 10


class PythonProfiler:
    BLACKLISTED_PYTHON_PROCS = ["unattended-upgrades", "networkd-dispatcher", "supervisord", "tuned"]

    def __init__(self, frequency: int, duration: int, stop_event: Event, storage_dir: str):
        self._frequency = min(frequency, PYTHON_PROFILER_MAX_FREQUENCY)
        self._duration = duration
        self._stop_event = stop_event
        self._storage_dir = storage_dir
        logger.info(f"Initializing Python profiler (frequency: {self._frequency}hz, duration: {duration}s)")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        pass

    def profile_process(self, pid: int, cmdline: str):
        logger.info(f"Profiling process {pid} ({cmdline})")

        local_output_path = os.path.join(self._storage_dir, f"{pid}.py.col.dat")
        try:
            run_process(
                [
                    resource_path("python/py-spy"),
                    "record",
                    "-r",
                    str(self._frequency),
                    "-d",
                    str(self._duration),
                    "--nonblocking",
                    "--format",
                    "raw",
                    "-F",
                    "--gil",
                    "--output",
                    local_output_path,
                    "-p",
                    str(pid),
                ],
                stop_event=self._stop_event,
            )
        except ProcessStoppedException:
            raise StopEventSetException

        logger.info(f"Finished profiling process {pid}")
        return parse_collapsed(Path(local_output_path).read_text())

    def find_python_processes_to_profile(self) -> Dict[str, str]:
        filtered_procs = {}
        for process in pgrep_exe(r"^.+/python[^/]*$"):
            try:
                if process.pid == os.getpid():
                    continue

                cmdline = process.cmdline()
                if any(item in cmdline for item in self.BLACKLISTED_PYTHON_PROCS):
                    continue

                filtered_procs[process.pid] = cmdline
            except Exception:
                logger.exception(f"Couldn't add pid {process.pid} to list")

        return filtered_procs

    def profile_processes(self):
        pids_to_profile = self.find_python_processes_to_profile()
        if not pids_to_profile:
            return {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(pids_to_profile)) as executor:
            futures = []
            for pid, cmdline in pids_to_profile.items():
                future = executor.submit(self.profile_process, pid, cmdline)
                future.pid = pid
                futures.append(future)

            results = {}
            for future in concurrent.futures.as_completed(futures):
                try:
                    results[future.pid] = future.result()
                except StopEventSetException:
                    raise
                except Exception:
                    logger.exception(f"Failed to profile Python process {future.pid}")

        return results
