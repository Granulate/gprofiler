#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from pathlib import Path
from threading import Event
from typing import List, Optional

from psutil import Process

from gprofiler.exceptions import ProcessStoppedException, StopEventSetException
from gprofiler.gprofiler_types import StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.merge import parse_one_collapsed_file
from gprofiler.profilers.profiler_base import ProcessProfilerBase
from gprofiler.profilers.registry import register_profiler
from gprofiler.utils import pgrep_maps, process_comm, random_prefix, removed_path, resource_path, run_process

logger = get_logger_adapter(__name__)


@register_profiler(
    "Ruby",
    possible_modes=["rbspy", "disabled"],
    supported_archs=["x86_64", "aarch64"],
    default_mode="rbspy",
)
class RbSpyProfiler(ProcessProfilerBase):
    RESOURCE_PATH = "ruby/rbspy"
    MAX_FREQUENCY = 100

    def __init__(self, frequency: int, duration: int, stop_event: Optional[Event], storage_dir: str, ruby_mode: str):
        super().__init__(frequency, duration, stop_event, storage_dir)
        assert ruby_mode == "rbspy", "Ruby profiler should not be initialized, wrong ruby_mode value given"

    def _make_command(self, pid: int, output_path: str) -> List[str]:
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
        logger.info(
            f"Profiling process {process.pid} with rbspy", cmdline=" ".join(process.cmdline()), no_extra_to_server=True
        )

        local_output_path = os.path.join(self._storage_dir, f"rbspy.{random_prefix()}.{process.pid}.col")
        with removed_path(local_output_path):
            try:
                run_process(self._make_command(process.pid, local_output_path), stop_event=self._stop_event)
            except ProcessStoppedException:
                raise StopEventSetException

            logger.info(f"Finished profiling process {process.pid} with rbspy")
            return parse_one_collapsed_file(Path(local_output_path), process_comm(process))

    def _select_processes_to_profile(self) -> List[Process]:
        return pgrep_maps(r"(?:^.+/ruby[^/]*$)")
