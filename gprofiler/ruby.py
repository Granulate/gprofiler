#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import concurrent.futures
import logging
import os
from pathlib import Path
from typing import List

from psutil import Process

from gprofiler.exceptions import ProcessStoppedException, StopEventSetException
from gprofiler.merge import parse_one_collapsed
from gprofiler.profiler_base import ProfilerBase
from gprofiler.types import ProcessToStackSampleCounters
from gprofiler.utils import pgrep_maps, random_prefix, resource_path, run_process

logger = logging.getLogger(__name__)


def _find_ruby_processes() -> List[Process]:
    return pgrep_maps(r"(?:^.+/ruby[^/]*$)")


class RbSpyProfiler(ProfilerBase):
    RESOURCE_PATH = "ruby/rbspy"
    MAX_FREQUENCY = 100

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
        logger.info(f"Profiling process {process.pid}", cmdline=' '.join(process.cmdline()), no_extra_to_server=True)
        comm = process.name()

        local_output_path = os.path.join(self._storage_dir, f"rbspy.{random_prefix()}.{process.pid}.col")
        try:
            run_process(self._make_command(process.pid, local_output_path), stop_event=self._stop_event)
        except ProcessStoppedException:
            raise StopEventSetException

        logger.info(f"Finished profiling process {process.pid} with rbspy")
        return parse_one_collapsed(Path(local_output_path).read_text(), comm)

    def snapshot(self) -> ProcessToStackSampleCounters:
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
