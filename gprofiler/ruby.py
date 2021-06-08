#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import concurrent.futures
import logging
import os
from pathlib import Path
from threading import Event
from typing import List, Mapping, Optional

from psutil import Process

from .exceptions import ProcessStoppedException, StopEventSetException
from .merge import parse_one_collapsed
from .profiler_base import ProfilerBase
from .utils import limit_frequency, pgrep_maps, resource_path, run_process

logger = logging.getLogger(__name__)


def _find_ruby_processes() -> List[Process]:
    return pgrep_maps(r"(?:^.+/ruby[^/]*$)")


class RbSpyProfiler(ProfilerBase):
    RESOURCE_PATH = "ruby/rbspy"
    MAX_FREQUENCY = 100

    def __init__(self, frequency: int, duration: int, stop_event: Optional[Event], storage_dir: str):
        super().__init__()
        assert isinstance(self.MAX_FREQUENCY, int)
        self._frequency = limit_frequency(self.MAX_FREQUENCY, frequency, self.__class__.__name__, logger)
        self._duration = duration
        self._stop_event = stop_event or Event()
        self._storage_dir = storage_dir
        logger.info(f"Initializing Ruby profiler (frequency: {self._frequency}hz, duration: {self._duration}s)")

    def _make_command(self, pid: int, output_path: str):
        return [
            resource_path(self.RESOURCE_PATH),
            "record",
            "-r",
            str(self._frequency),
            "-d",
            str(self._duration),
            "--nonblocking",  # Don’t pause the ruby process when collecting stack samples.
            "--on-cpu",  # only record when CPU is active
            "--format=collapsed",
            "--file",
            output_path,
            "--raw-file",
            "/dev/null",  # We don't need that file and there is no other way to avoid creating it
            "-p",
            str(pid),
        ]

    def _profile_process(self, process: Process):
        logger.info(f"Profiling process {process.pid} ({' '.join(process.cmdline())})")

        local_output_path = os.path.join(self._storage_dir, f"{process.pid}.col")
        try:
            run_process(self._make_command(process.pid, local_output_path), stop_event=self._stop_event)
        except ProcessStoppedException:
            raise StopEventSetException

        logger.info(f"Finished profiling process {process.pid} with rbspy")
        return parse_one_collapsed(Path(local_output_path).read_text())

    def snapshot(self) -> Mapping[int, Mapping[str, int]]:
        processes_to_profile = _find_ruby_processes()
        if not processes_to_profile:
            return {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(processes_to_profile)) as executor:
            futures = {}
            for process in processes_to_profile:
                futures[executor.submit(self._profile_process, process)] = process.pid

            results = {}
            for future in concurrent.futures.as_completed(futures):
                try:
                    results[futures[future]] = future.result()
                except StopEventSetException:
                    raise
                except Exception:
                    logger.exception(f"Failed to profile Ruby process {futures[future]}")

        return results
