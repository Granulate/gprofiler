#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import signal
from pathlib import Path
from subprocess import CompletedProcess
from threading import Event
from typing import Any, Dict, List, Optional

from granulate_utils.exceptions import ProcessStoppedException, StopEventSetException
from granulate_utils.linux.ns import get_process_nspid, run_in_ns
from psutil import Process

from gprofiler import merge
from gprofiler.gprofiler_types import StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.profilers.profiler_base import ProcessProfilerBase
from gprofiler.profilers.registry import register_profiler
from gprofiler.utils import pgrep_maps, random_prefix, removed_path, resource_path, run_process_logged
from gprofiler.utils.elf import get_elf_id, get_mapped_dso_elf_id
from gprofiler.utils.process import process_comm

logger = get_logger_adapter(__name__)


class RubyMetadata(ApplicationMetadata):
    _RUBY_VERSION_TIMEOUT = 3

    def _get_ruby_version(self, process: Process) -> str:
        if not os.path.basename(process.exe()).startswith("ruby"):
            # TODO: for dynamic executables, find the ruby binary that works with the loaded libruby, and
            # check it instead. For static executables embedding libruby - :shrug:
            raise NotImplementedError

        ruby_path = f"/proc/{get_process_nspid(process.pid)}/exe"

        def _run_ruby_version() -> "CompletedProcess[bytes]":
            return run_process_logged(
                [
                    ruby_path,
                    "--version",
                ],
                stop_event=self._stop_event,
                timeout=self._RUBY_VERSION_TIMEOUT,
            )

        return run_in_ns(["pid", "mnt"], _run_ruby_version, process.pid).stdout.decode().strip()

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
class RbSpyProfiler(ProcessProfilerBase):
    RESOURCE_PATH = "ruby/rbspy"
    MAX_FREQUENCY = 100
    _EXTRA_TIMEOUT = 10  # extra time like given to py-spy

    def __init__(self, frequency: int, duration: int, stop_event: Optional[Event], storage_dir: str, ruby_mode: str):
        super().__init__(frequency, duration, stop_event, storage_dir)
        assert ruby_mode == "rbspy", "Ruby profiler should not be initialized, wrong ruby_mode value given"
        self._metadata = RubyMetadata(self._stop_event)

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
        self._metadata.update_metadata(process)

        local_output_path = os.path.join(self._storage_dir, f"rbspy.{random_prefix()}.{process.pid}.col")
        with removed_path(local_output_path):
            try:
                run_process_logged(
                    self._make_command(process.pid, local_output_path),
                    stop_event=self._stop_event,
                    timeout=self._duration + self._EXTRA_TIMEOUT,
                    kill_signal=signal.SIGKILL,
                )
            except ProcessStoppedException:
                raise StopEventSetException

            logger.info(f"Finished profiling process {process.pid} with rbspy")
            return merge.parse_one_collapsed_file(Path(local_output_path), process_comm(process))

    def _select_processes_to_profile(self) -> List[Process]:
        return pgrep_maps(r"(?:^.+/ruby[^/]*$)")
