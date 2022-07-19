#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import signal
from pathlib import Path
from subprocess import Popen
from threading import Event
from typing import Any, Dict, List, Optional

from granulate_utils.linux.elf import get_elf_id, get_mapped_dso_elf_id
from granulate_utils.linux.ns import get_process_nspid, run_in_ns
from psutil import Process

from gprofiler import merge
from gprofiler.exceptions import ProcessStoppedException, StopEventSetException
from gprofiler.gprofiler_types import ProfileData
from gprofiler.log import get_logger_adapter
from gprofiler.profilers.profiler_base import ProcessProfilerBase
from gprofiler.profilers.registry import register_profiler
from gprofiler.utils import pgrep_maps, random_prefix, removed_path, resource_path, run_process
from gprofiler.utils.process import process_comm

logger = get_logger_adapter(__name__)

# class DotnetMetadata(ApplicationMetadata):
#     _DOTNET_VERSION_TIMEOUT = 3

#     def _get_dotnet_version(self, process: Process) -> str:
#         if not os.path.basename(process.exe()).startswith("ruby"):
#             # TODO: for dynamic executables, find the ruby binary that works with the loaded libruby, and
#             # check it instead. For static executables embedding libruby - :shrug:
#             raise NotImplementedError

#         dotnet_path = f"/proc/{get_process_nspid(process.pid)}/exe"

#         def _run_ruby_version() -> "CompletedProcess[bytes]":
#             return run_process(
#                 [
#                     dotnet_path,
#                     "--version",
#                 ],
#                 stop_event=self._stop_event,
#                 timeout=self._RUBY_VERSION_TIMEOUT,
#             )

#         return run_in_ns(["pid", "mnt"], _run_ruby_version, process.pid).stdout.decode().strip()

#     def make_application_metadata(self, process: Process) -> Dict[str, Any]:
#         # ruby version
#         version = self._get_dotnet_version(process)




@register_profiler(
    "dotnet",
    possible_modes=["dotnet-trace", "disabled"],
    supported_archs=["x86_64", "aarch64"],
    default_mode="dotnet-trace",
)
class DotnetProfiler(ProcessProfilerBase):
    
    RESOURCE_PATH = "dotnet/tools/dotnet-trace"
    MAX_FREQUENCY = 100
    _EXTRA_TIMEOUT = 10 

    def __init__(self, frequency: int, duration: int, stop_event: Optional[Event], storage_dir: str, dotnet_mode: str):
        super().__init__(frequency, duration, stop_event, storage_dir)
        self._process: Optional[Popen] = None
        assert dotnet_mode == "dotnet-trace", "Dotnet profiler should not be initialized, wrong dotnet-trace value given"
#        self._metadata = DotnetMetadata(self._stop_event)

    def _make_command(self, pid: int, output_path: str) -> List[str]:
        return [
            resource_path(self.RESOURCE_PATH),
            "collect",
            "--format",
            "speedscope",
            "--process-id",
 #           str(self._frequency),
            str(pid),
        ]

    def _profile_process(self, process: Process) -> ProfileData:
        logger.info(
            f"Profiling process {process.pid} with dotnet-trace", cmdline=" ".join(process.cmdline()), no_extra_to_server=True
        )
        comm = process_comm(process)
        ## app_metadata = self._metadata.get_metadata(process)
        #appid = None
        local_output_path = os.path.join(self._storage_dir, f"dotnet-trace.{random_prefix()}.{process.pid}.col")
        #logger.info(str(resource_path(self.RESOURCE_PATH)))
        with removed_path(local_output_path):
            try:
                run_process(
                    self._make_command(process.pid, local_output_path),
                    stop_event=self._stop_event,
                    timeout=self._duration + self._EXTRA_TIMEOUT,
                    kill_signal=signal.SIGKILL,
                )
            except ProcessStoppedException:
                raise StopEventSetException

            logger.info(f"Finished profiling process {process.pid} with dotnet")
            return ProfileData(merge.parse_one_collapsed_file(Path(local_output_path), comm))

    def _parse_speedscope():
        return "profile data"
    
    def _select_processes_to_profile(self) -> List[Process]:
        return pgrep_maps(r"(?:^.+/dotnet[^/]*$)")
