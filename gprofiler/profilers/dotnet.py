#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import datetime
import functools
import os
import signal
from threading import Event
from typing import Any, Dict, List, Optional

from granulate_utils.linux.ns import get_process_nspid
from granulate_utils.linux.process import process_exe
from psutil import Process

from gprofiler.exceptions import ProcessStoppedException, StopEventSetException
from gprofiler.gprofiler_types import ProfileData
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.profilers.profiler_base import ProcessProfilerBase
from gprofiler.profilers.registry import register_profiler
from gprofiler.utils import pgrep_maps, random_prefix, removed_path, resource_path, run_process
from gprofiler.utils.process import process_comm
from gprofiler.utils.speedscope import load_speedscope_as_collapsed

logger = get_logger_adapter(__name__)


class DotnetMetadata(ApplicationMetadata):
    _DOTNET_VERSION_TIMEOUT = 3

    @functools.lru_cache(4096)
    def _get_dotnet_version(self, process: Process) -> str:
        if not os.path.basename(process_exe(process)).startswith("dotnet"):
            raise NotImplementedError
        version = self.get_exe_version(process)  # not using cached version here since this wrapper is a cache
        return version

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        # dotnet version
        try:
            version = self._get_dotnet_version(process)
            metadata = {"dotnet_version": version}
        except Exception:
            metadata = {}

        metadata.update(super().make_application_metadata(process))
        return metadata


@register_profiler(
    "dotnet",
    possible_modes=["dotnet-trace", "disabled"],
    supported_archs=["x86_64"],
    default_mode="dotnet-trace",
)
class DotnetProfiler(ProcessProfilerBase):
    RESOURCE_PATH = "dotnet/tools/dotnet-trace"
    _EXTRA_TIMEOUT = 10

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Optional[Event],
        storage_dir: str,
        profile_spawned_processes: bool,
        dotnet_mode: str,
    ):
        super().__init__(frequency, duration, stop_event, storage_dir)
        assert (
            dotnet_mode == "dotnet-trace"
        ), "Dotnet profiler should not be initialized, wrong dotnet-trace value given"
        self._metadata = DotnetMetadata(self._stop_event)

    def _make_command(self, process: Process, duration: int, output_path: str) -> List[str]:
        if duration > 3600 * 24:
            raise ValueError("Duration exceeds one full day")
        return [
            resource_path(self.RESOURCE_PATH),
            "collect",
            "--format",
            "speedscope",
            "--process-id",
            str(get_process_nspid(process.pid)),
            "--profile",
            "cpu-sampling",
            "--duration",
            str(datetime.timedelta(seconds=duration)),
            "--output",
            output_path,
            #           str(self._frequency), - TODO: frequency handling to be determined
        ]

    def _profile_process(self, process: Process, duration: int, spawned: bool) -> ProfileData:
        logger.info(
            f"Profiling{' spawned' if spawned else ''} process {process.pid} with dotnet-trace",
            cmdline=" ".join(process.cmdline()),
            no_extra_to_server=True,
        )
        appid = None
        app_metadata = self._metadata.get_metadata(process)
        # had to change the dots for minuses because of dotnet-trace removing the last part in other case
        local_output_path = os.path.join(self._storage_dir, f"dotnet-trace-{random_prefix()}-{process.pid}")
        # this causes dotnet-trace to lookup the socket in the mount namespace of the target process
        tempdir = f"/proc/{process.pid}/root/tmp"
        with removed_path(local_output_path):
            try:
                run_process(
                    self._make_command(process, duration, local_output_path),
                    env={"TMPDIR": tempdir},
                    stop_event=self._stop_event,
                    timeout=self._duration + self._EXTRA_TIMEOUT,
                    kill_signal=signal.SIGKILL,
                )
                local_output_path = local_output_path + ".speedscope.json"
            except ProcessStoppedException:
                raise StopEventSetException
            logger.info(f"Finished profiling process {process.pid} with dotnet")
            comm = process_comm(process)
            return ProfileData(
                load_speedscope_as_collapsed(local_output_path, self._frequency, comm), appid, app_metadata
            )

    def _select_processes_to_profile(self) -> List[Process]:
        return pgrep_maps(r"(?:^.+/dotnet[^/]*$)")
