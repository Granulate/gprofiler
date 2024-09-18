#
# Copyright (C) 2022 Intel Corporation
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

import os
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from granulate_utils.exceptions import MissingExePath
from granulate_utils.golang import get_process_golang_version, is_golang_process
from granulate_utils.linux.elf import elf_is_stripped, is_statically_linked
from granulate_utils.linux.process import is_musl, is_process_running
from granulate_utils.node import is_node_process
from psutil import NoSuchProcess, Process

from gprofiler import merge
from granulate_utils.gprofiler.exceptions import PerfNoSupportedEvent, StopEventSetException
from gprofiler.gprofiler_types import (
    ProcessToProfileData,
    ProcessToStackSampleCounters,
    ProfileData,
    StackToSampleCount,
)
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import ProfileMetadata, application_identifiers
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.node import clean_up_node_maps, generate_map_for_node_processes, get_node_processes
from gprofiler.profilers.profiler_base import ProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils.perf import discover_appropriate_perf_event, parse_perf_script, valid_perf_pid
from gprofiler.utils.perf_process import PerfProcess

logger = get_logger_adapter(__name__)

DEFAULT_PERF_DWARF_STACK_SIZE = 8192


def get_average_frame_count(samples: Iterable[str]) -> float:
    """
    Get the average frame count for all samples.
    Avoids counting kernel frames because this function is used to determine whether FP stacks
    or DWARF stacks are to be used. FP stacks are collected regardless of FP or DWARF, so we don't
    count them in this heuristic.
    """
    frame_count_per_samples = []
    for sample in samples:
        kernel_split = sample.split("_[k];", 1)
        if len(kernel_split) == 1:
            kernel_split = sample.split("_[k] ", 1)

        # Check if there are any "[unknown]" frames in this sample, if they are ignore it. This is helpful
        # in perf_mode="smart", because DWARF stack can be deep but some of the frames might be unknown. In
        # that situation we want to avoid counting unknown frames and then choose stacks that has better
        # sum(frame_count_per_samples)/len(frame_count_per_samples) ratio
        frame_count_in_sample = kernel_split[0].count(";") - kernel_split[0].count("[unknown]")
        # Do we have any kernel frames in this sample?
        if len(kernel_split) > 1:
            # example: "a;b;c;d_[k];e_[k] 1" should return the same value as "a;b;c 1", so we don't
            # add 1 to the frames count like we do in the other branch.
            frame_count_per_samples.append(frame_count_in_sample)
        else:
            # no kernel frames, so e.g "a;b;c 1" and frame count is one more than ";" count.
            frame_count_per_samples.append(frame_count_in_sample + 1)
    return sum(frame_count_per_samples) / len(frame_count_per_samples)


def add_highest_avg_depth_stacks_per_process(
    dwarf_perf: ProcessToStackSampleCounters,
    fp_perf: ProcessToStackSampleCounters,
    fp_to_dwarf_sample_ratio: float,
    merged_pid_to_stacks_counters: ProcessToStackSampleCounters,
) -> None:
    for pid, fp_collapsed_stacks_counters in fp_perf.items():
        if pid not in dwarf_perf:
            merged_pid_to_stacks_counters[pid] = fp_collapsed_stacks_counters
            continue

        fp_frame_count_average = get_average_frame_count(fp_collapsed_stacks_counters.keys())
        dwarf_collapsed_stacks_counters = dwarf_perf[pid]
        dwarf_frame_count_average = get_average_frame_count(dwarf_collapsed_stacks_counters.keys())
        if fp_frame_count_average > dwarf_frame_count_average:
            merged_pid_to_stacks_counters[pid] = fp_collapsed_stacks_counters
        else:
            dwarf_collapsed_stacks_counters = merge.scale_sample_counts(
                dwarf_collapsed_stacks_counters, fp_to_dwarf_sample_ratio
            )
            merged_pid_to_stacks_counters[pid] = dwarf_collapsed_stacks_counters


def merge_global_perfs(
    raw_fp_perf: Optional[str], raw_dwarf_perf: Optional[str], insert_dso_name: bool = False
) -> ProcessToStackSampleCounters:
    fp_perf = parse_perf_script(raw_fp_perf, insert_dso_name)
    dwarf_perf = parse_perf_script(raw_dwarf_perf, insert_dso_name)

    if raw_fp_perf is None:
        return dwarf_perf
    elif raw_dwarf_perf is None:
        return fp_perf

    total_fp_samples = sum([sum(stacks.values()) for stacks in fp_perf.values()])
    total_dwarf_samples = sum([sum(stacks.values()) for stacks in dwarf_perf.values()])
    if total_dwarf_samples == 0:
        fp_to_dwarf_sample_ratio = 0.0  # ratio can be 0 because if total_dwarf_samples is 0 then it will be never used
    else:
        fp_to_dwarf_sample_ratio = total_fp_samples / total_dwarf_samples

    # The FP perf is used here as the "main" perf, to which the DWARF perf is scaled.
    merged_pid_to_stacks_counters: ProcessToStackSampleCounters = defaultdict(Counter)
    add_highest_avg_depth_stacks_per_process(
        dwarf_perf, fp_perf, fp_to_dwarf_sample_ratio, merged_pid_to_stacks_counters
    )
    total_merged_samples = sum([sum(stacks.values()) for stacks in merged_pid_to_stacks_counters.values()])
    logger.debug(
        f"Total FP samples: {total_fp_samples}; Total DWARF samples: {total_dwarf_samples}; "
        f"FP to DWARF ratio: {fp_to_dwarf_sample_ratio}; Total merged samples: {total_merged_samples}"
    )
    return merged_pid_to_stacks_counters


@register_profiler(
    "Perf",
    possible_modes=["fp", "dwarf", "smart", "disabled"],
    default_mode="fp",
    supported_archs=["x86_64", "aarch64"],
    profiler_mode_argument_help="Run perf with either FP (Frame Pointers), DWARF, or run both and intelligently merge"
    " them by choosing the best result per process. If 'disabled' is chosen, do not invoke"
    " 'perf' at all. The output, in that case, is the concatenation of the results from all"
    " of the runtime profilers. Defaults to 'smart'.",
    profiler_arguments=[
        ProfilerArgument(
            "--perf-dwarf-stack-size",
            help="The max stack size for the Dwarf perf, in bytes. Must be <=65528."
            " Relevant for --perf-mode dwarf|smart. Default: %(default)s",
            type=int,
            default=DEFAULT_PERF_DWARF_STACK_SIZE,
            dest="perf_dwarf_stack_size",
        ),
        ProfilerArgument(
            "--perf-no-memory-restart",
            help="Disable checking if perf used memory exceeds threshold and restarting perf",
            action="store_false",
            dest="perf_memory_restart",
        ),
    ],
    disablement_help="Disable the global perf of processes,"
    " and instead only concatenate runtime-specific profilers results",
    supported_profiling_modes=["cpu"],
)
class SystemProfiler(ProfilerBase):
    """
    We are running 2 perfs in parallel - one with DWARF and one with FP, and then we merge their results.
    This improves the results from software that is compiled without frame pointers,
    like some native software. DWARF by itself is not good enough, as it has issues with unwinding some
    versions of Go processes.
    """

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        perf_mode: str,
        perf_dwarf_stack_size: int,
        perf_inject: bool,
        perf_node_attach: bool,
        perf_memory_restart: bool,
    ):
        super().__init__(frequency, duration, profiler_state)
        self._perfs: List[PerfProcess] = []
        self._metadata_collectors: List[PerfMetadata] = [
            GolangPerfMetadata(self._profiler_state.stop_event),
            NodePerfMetadata(self._profiler_state.stop_event),
        ]
        self._node_processes: List[Process] = []
        self._node_processes_attached: List[Process] = []
        self._perf_memory_restart = perf_memory_restart
        switch_timeout_s = duration * 3  # allow gprofiler to be delayed up to 3 intervals before timing out.
        extra_args = []
        try:
            # We want to be certain that `perf record` will collect samples.
            discovered_perf_event = discover_appropriate_perf_event(
                Path(self._profiler_state.storage_dir), self._profiler_state.stop_event
            )
            logger.debug("Discovered perf event", discovered_perf_event=discovered_perf_event.name)
            extra_args.extend(discovered_perf_event.perf_extra_args())
        except PerfNoSupportedEvent:
            logger.critical("Failed to determine perf event to use")
            raise

        if perf_mode in ("fp", "smart"):
            self._perf_fp: Optional[PerfProcess] = PerfProcess(
                frequency=self._frequency,
                stop_event=self._profiler_state.stop_event,
                output_path=os.path.join(self._profiler_state.storage_dir, "perf.fp"),
                is_dwarf=False,
                inject_jit=perf_inject,
                extra_args=extra_args,
                processes_to_profile=self._profiler_state.processes_to_profile,
                switch_timeout_s=switch_timeout_s,
            )
            self._perfs.append(self._perf_fp)
        else:
            self._perf_fp = None

        if perf_mode in ("dwarf", "smart"):
            extra_args.extend(["--call-graph", f"dwarf,{perf_dwarf_stack_size}"])
            self._perf_dwarf: Optional[PerfProcess] = PerfProcess(
                frequency=self._frequency,
                stop_event=self._profiler_state.stop_event,
                output_path=os.path.join(self._profiler_state.storage_dir, "perf.dwarf"),
                is_dwarf=True,
                inject_jit=False,  # no inject in dwarf mode, yet
                extra_args=extra_args,
                processes_to_profile=self._profiler_state.processes_to_profile,
                switch_timeout_s=switch_timeout_s,
            )
            self._perfs.append(self._perf_dwarf)
        else:
            self._perf_dwarf = None

        self.perf_node_attach = perf_node_attach
        assert self._perf_fp is not None or self._perf_dwarf is not None

    def start(self) -> None:
        # we have to also generate maps here,
        # it might be too late for first round to generate it in snapshot()
        if self.perf_node_attach:
            self._node_processes = get_node_processes()
            self._node_processes_attached.extend(generate_map_for_node_processes(self._node_processes))
        for perf in self._perfs:
            perf.start()

    def stop(self) -> None:
        if self.perf_node_attach:
            self._node_processes = [process for process in self._node_processes if is_process_running(process)]
            clean_up_node_maps(self._node_processes_attached)
        for perf in reversed(self._perfs):
            perf.stop()

    def _get_metadata(self, pid: int) -> Optional[ProfileMetadata]:
        if not valid_perf_pid(pid):
            return None

        try:
            process = Process(pid)
            for collector in self._metadata_collectors:
                if collector.relevant_for_process(process):
                    return collector.get_metadata(process)
        except NoSuchProcess:
            pass
        except MissingExePath:
            pass
        return None

    def _get_appid(self, pid: int) -> Optional[str]:
        try:
            process = Process(pid)
            return application_identifiers.get_node_app_id(process)
        except NoSuchProcess:
            pass
        return None

    def snapshot(self) -> ProcessToProfileData:
        if self.perf_node_attach:
            self._node_processes = [process for process in self._node_processes if is_process_running(process)]
            new_processes = [process for process in get_node_processes() if process not in self._node_processes]
            self._node_processes_attached.extend(generate_map_for_node_processes(new_processes))
            self._node_processes.extend(new_processes)

        # if process is stopped for whatever reason - restart
        for perf in self._perfs:
            perf.restart_if_not_running()

        if self._perf_memory_restart:
            for perf in self._perfs:
                perf.restart_if_rss_exceeded()

        if self._profiler_state.stop_event.wait(self._duration):
            raise StopEventSetException

        for perf in self._perfs:
            perf.switch_output()

        data = {
            k: self._generate_profile_data(v, k)
            for k, v in merge_global_perfs(
                self._perf_fp.wait_and_script() if self._perf_fp is not None else None,
                self._perf_dwarf.wait_and_script() if self._perf_dwarf is not None else None,
                self._profiler_state.insert_dso_name,
            ).items()
        }

        return data

    def _generate_profile_data(self, stacks: StackToSampleCount, pid: int) -> ProfileData:
        metadata = self._get_metadata(pid)
        if metadata is not None and "node_version" in metadata:
            appid = self._get_appid(pid)
        else:
            appid = None
        return ProfileData(stacks, appid, metadata, self._profiler_state.get_container_name(pid))


class PerfMetadata(ApplicationMetadata):
    def relevant_for_process(self, process: Process) -> bool:
        return False

    def add_exe_metadata(self, process: Process, metadata: Dict[str, Any]) -> None:
        static = is_statically_linked(f"/proc/{process.pid}/exe")
        exe_metadata: Dict[str, Any] = {"link": "static" if static else "dynamic"}
        if not static:
            exe_metadata["libc"] = "musl" if is_musl(process) else "glibc"
        else:
            exe_metadata["libc"] = None

        metadata.update(exe_metadata)


class GolangPerfMetadata(PerfMetadata):
    def relevant_for_process(self, process: Process) -> bool:
        return is_golang_process(process)

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        metadata = {
            "golang_version": get_process_golang_version(process),
            "stripped": elf_is_stripped(f"/proc/{process.pid}/exe"),
        }
        self.add_exe_metadata(process, metadata)
        metadata.update(super().make_application_metadata(process))
        return metadata


class NodePerfMetadata(PerfMetadata):
    def relevant_for_process(self, process: Process) -> bool:
        return is_node_process(process)

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        metadata = {"node_version": self.get_exe_version_cached(process)}
        self.add_exe_metadata(process, metadata)
        metadata.update(super().make_application_metadata(process))
        return metadata
