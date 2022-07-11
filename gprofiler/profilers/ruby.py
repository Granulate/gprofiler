#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import functools
import os
import re
import signal
from pathlib import Path
from threading import Event
from typing import Any, Dict, List, Optional

from granulate_utils.linux.elf import get_elf_id, get_mapped_dso_elf_id
from granulate_utils.linux.process import process_exe
from psutil import Process

from gprofiler import merge
from gprofiler.exceptions import ProcessStoppedException, StopEventSetException
from gprofiler.gprofiler_types import ProfileData
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.profilers.profiler_base import SpawningProcessProfilerBase
from gprofiler.profilers.registry import register_profiler
from gprofiler.utils import pgrep_maps, random_prefix, removed_path, resource_path, run_process
from gprofiler.utils.process import process_comm, read_proc_file

logger = get_logger_adapter(__name__)


class RubyMetadata(ApplicationMetadata):
    _RUBY_VERSION_TIMEOUT = 3

    @functools.lru_cache(4096)
    def _get_ruby_version(self, process: Process) -> str:
        if not os.path.basename(process_exe(process)).startswith("ruby"):
            # TODO: for dynamic executables, find the ruby binary that works with the loaded libruby, and
            # check it instead. For static executables embedding libruby - :shrug:
            raise NotImplementedError
        version = self.get_exe_version(process)  # not using cached version here since this wrapper is a cache
        return version

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        # ruby version
        version = self._get_ruby_version(process)

        # ruby elfid & libruby elfid, if exists
        exe_elfid = get_elf_id(f"/proc/{process.pid}/exe")
        libruby_elfid = get_mapped_dso_elf_id(process, "/libruby")

        metadata = {"ruby_version": version, "exe_elfid": exe_elfid, "libruby_elfid": libruby_elfid}

        metadata.update(super().make_application_metadata(process))
        return metadata


@register_profiler(
    "Ruby",
    possible_modes=["rbspy", "disabled"],
    supported_archs=["x86_64", "aarch64"],
    default_mode="rbspy",
)
class RbSpyProfiler(SpawningProcessProfilerBase):
    RESOURCE_PATH = "ruby/rbspy"
    MAX_FREQUENCY = 100
    _EXTRA_TIMEOUT = 10  # extra time like given to py-spy
    DETECTED_RUBY_PROCESSES_REGEX = r"(?:^.+/ruby[^/]*$)"

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Optional[Event],
        storage_dir: str,
        profile_spawned_processes: bool,
        ruby_mode: str,
    ):
        super().__init__(frequency, duration, stop_event, storage_dir, profile_spawned_processes)
        assert ruby_mode == "rbspy", "Ruby profiler should not be initialized, wrong ruby_mode value given"
        self._metadata = RubyMetadata(self._stop_event)

    def _make_command(self, pid: int, output_path: str, duration: int) -> List[str]:
        return [
            resource_path(self.RESOURCE_PATH),
            "record",
            "--silent",
            "-r",
            str(self._frequency),
            "-d",
            str(duration),
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

    def _profile_process(self, process: Process, duration: int) -> ProfileData:
        logger.info(
            f"Profiling process {process.pid} with rbspy", cmdline=" ".join(process.cmdline()), no_extra_to_server=True
        )
        comm = process_comm(process)
        app_metadata = self._metadata.get_metadata(process)
        appid = None  # TODO: implement appids for Ruby

        local_output_path = os.path.join(self._storage_dir, f"rbspy.{random_prefix()}.{process.pid}.col")
        with removed_path(local_output_path):
            try:
                run_process(
                    self._make_command(process.pid, local_output_path, duration),
                    stop_event=self._stop_event,
                    timeout=duration + self._EXTRA_TIMEOUT,
                    kill_signal=signal.SIGKILL,
                )
            except ProcessStoppedException:
                raise StopEventSetException

            logger.info(f"Finished profiling process {process.pid} with rbspy")
            return ProfileData(merge.parse_one_collapsed_file(Path(local_output_path), comm), appid, app_metadata)

    def _select_processes_to_profile(self) -> List[Process]:
        return pgrep_maps(self.DETECTED_RUBY_PROCESSES_REGEX)

    def _should_profile_process(self, process: Process) -> bool:
        return any(
            re.match(self.DETECTED_RUBY_PROCESSES_REGEX, line) for line in read_proc_file(process, "maps").splitlines()
        )
