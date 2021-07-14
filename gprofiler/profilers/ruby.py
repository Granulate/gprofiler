#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from pathlib import Path
from typing import List

from psutil import Process

from gprofiler.exceptions import ProcessStoppedException, StopEventSetException
from gprofiler.log import get_logger_adapter
from gprofiler.merge import parse_and_remove_one_collapsed
from gprofiler.profilers.profiler_base import ProcessProfilerBase
from gprofiler.profilers.registry import register_profiler
from gprofiler.types import StackToSampleCount
from gprofiler.utils import pgrep_maps, random_prefix, resource_path, run_process

logger = get_logger_adapter(__name__)


@register_profiler("Ruby")
class RbSpyProfiler(ProcessProfilerBase):
    RESOURCE_PATH = "ruby/rbspy"
    MAX_FREQUENCY = 100

    def _make_command(self, pid: int, output_path: str):
        return [
            resource_path(self.RESOURCE_PATH),
            "record",
            "--silent",
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

    def _profile_process(self, process: Process) -> StackToSampleCount:
        logger.info(f"Profiling process {process.pid}", cmdline=' '.join(process.cmdline()), no_extra_to_server=True)
        comm = process.name()

        local_output_path = os.path.join(self._storage_dir, f"rbspy.{random_prefix()}.{process.pid}.col")
        try:
            run_process(self._make_command(process.pid, local_output_path), stop_event=self._stop_event)
        except ProcessStoppedException:
            raise StopEventSetException

        logger.info(f"Finished profiling process {process.pid} with rbspy")
        return parse_and_remove_one_collapsed(Path(local_output_path), comm)

    def _select_processes_to_profile(self) -> List[Process]:
        return pgrep_maps(r"(?:^.+/ruby[^/]*$)")
