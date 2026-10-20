#
# Copyright (C) 2026 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from typing import Optional

from packaging.version import Version

from gprofiler.consts import GPU_PROFILING_MODE, MAX_GPU_PROFILING_FREQUENCY
from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import ProcessToProfileData, ProfileData, positive_integer
from gprofiler.metadata import ProfileMetadata
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.profiler_base import SystemProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import run_process
from gprofiler.utils.iaprof_process import IaprofProcess, IaprofProcessError

IAPROF_SNAPSHOT_TIMEOUT = 5


def frequency_to_iaprof_interval(frequency: int) -> int:
    if not 1 <= frequency <= MAX_GPU_PROFILING_FREQUENCY:
        raise ValueError(f"iaprof frequency must be between 1 and {MAX_GPU_PROFILING_FREQUENCY}")
    return round(1000 / frequency)


@register_profiler(
    "Iaprof",
    possible_modes=["enabled", "disabled"],
    default_mode="enabled",
    supported_archs=["x86_64"],  # not confirmed working on ARM, but should in theory
    supported_profiling_modes=[GPU_PROFILING_MODE],
    profiler_mode_argument_help="Enable or disable Intel GPU profiling with iaprof",
    disablement_help="Disable Intel GPU profiling with iaprof",
    profiler_arguments=[
        ProfilerArgument(
            "--iaprof-path",
            dest="iaprof_path",
            default="iaprof",
            help="Path to the iaprof executable. Default: %(default)s",
        ),
        ProfilerArgument(
            "--iaprof-eu-stall-subsample",
            dest="iaprof_eu_stall_subsample",
            type=positive_integer,
            default=100,
            help="Process one out of every N EU stall samples. Default: %(default)s",
        ),
    ],
)
class IaprofProfiler(SystemProfilerBase):
    MINIMUM_SUPPORTED_VERSION = Version("2026.3")
    _VERSION_TIMEOUT = 5

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        iaprof_mode: str,
        iaprof_path: str,
        iaprof_eu_stall_subsample: int,
        min_duration: int = 0,
    ) -> None:
        assert iaprof_mode == "enabled"
        super().__init__(frequency, duration, profiler_state, min_duration)
        self._iaprof_path = iaprof_path
        self._iaprof_version: Optional[str] = None
        self._output_interval_ms = frequency_to_iaprof_interval(frequency)
        self._eu_stall_subsample = iaprof_eu_stall_subsample
        self._iaprof = IaprofProcess(
            self._iaprof_path,
            self._output_interval_ms,
            self._eu_stall_subsample,
        )

    def get_profile_metadata(self) -> ProfileMetadata:
        assert self._iaprof_version is not None
        return {
            "iaprof_version": self._iaprof_version,
            "iaprof_output_interval_ms": self._output_interval_ms,
            "iaprof_eu_stall_subsample": self._eu_stall_subsample,
        }

    def _get_iaprof_version(self) -> str:
        result = run_process(
            [self._iaprof_path, "--version"],
            stop_event=self._profiler_state.stop_event,
            timeout=self._VERSION_TIMEOUT,
            pdeathsigger=False,
        )
        version_output = result.stdout.decode().strip() or result.stderr.decode().strip()
        version = Version(version_output)
        if version < self.MINIMUM_SUPPORTED_VERSION:
            raise RuntimeError(f"iaprof {version} is unsupported; minimum version is {self.MINIMUM_SUPPORTED_VERSION}")
        return str(version)

    def start(self) -> None:
        self._iaprof_version = self._get_iaprof_version()
        self._iaprof.start()

    def stop(self) -> None:
        self._iaprof.stop()

    def snapshot(self) -> ProcessToProfileData:
        self._iaprof.restart_if_not_running()

        if self._profiler_state.stop_event.wait(self._duration):
            raise StopEventSetException

        try:
            stacks_by_pid = self._iaprof.snapshot(IAPROF_SNAPSHOT_TIMEOUT)
        except IaprofProcessError:
            if self._profiler_state.stop_event.is_set():
                raise StopEventSetException
            raise

        return {
            pid: ProfileData(stacks, None, None, self._profiler_state.get_container_name(pid))
            for pid, stacks in stacks_by_pid.items()
        }
