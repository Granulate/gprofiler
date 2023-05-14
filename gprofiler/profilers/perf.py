#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import os
import re
import signal
import time
from collections import Counter, defaultdict
from pathlib import Path
from subprocess import Popen
from threading import Event
from typing import Any, Dict, Iterable, List, Optional

from granulate_utils.golang import get_process_golang_version, is_golang_process
from granulate_utils.linux.elf import is_statically_linked
from granulate_utils.linux.process import is_musl, is_process_running
from granulate_utils.node import is_node_process
from psutil import NoSuchProcess, Process

from gprofiler import merge
from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import (
    AppMetadata,
    ProcessToProfileData,
    ProcessToStackSampleCounters,
    ProfileData,
    StackToSampleCount,
)
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import application_identifiers
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.node import clean_up_node_maps, generate_map_for_node_processes, get_node_processes
from gprofiler.profilers.profiler_base import ProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import (
    reap_process,
    remove_files_by_prefix,
    remove_path,
    run_process,
    start_process,
    wait_event,
    wait_for_file_by_prefix,
)
from gprofiler.utils.perf import perf_path, valid_perf_pid

logger = get_logger_adapter(__name__)

DEFAULT_PERF_DWARF_STACK_SIZE = 8192
# ffffffff81082227 mmput+0x57 ([kernel.kallsyms])
# 0 [unknown] ([unknown])
# 7fe48f00faff __poll+0x4f (/lib/x86_64-linux-gnu/libc-2.31.so)
FRAME_REGEX = re.compile(r"^\s*[0-9a-f]+ (.*?) \(\[?(.*?)\]?\)$")
SAMPLE_REGEX = re.compile(
    r"\s*(?P<comm>.+?)\s+(?P<pid>[\d-]+)/(?P<tid>[\d-]+)(?:\s+\[(?P<cpu>\d+)])?\s+(?P<time>\d+\.\d+):\s+"
    r"(?:(?P<freq>\d+)\s+)?(?P<event_family>[\w\-_/]+):(?:(?P<event>[\w-]+):)?(?P<suffix>[^\n]*)(?:\n(?P<stack>.*))?",
    re.MULTILINE | re.DOTALL,
)


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

        # Do we have any kernel frames in this sample?
        if len(kernel_split) > 1:
            # example: "a;b;c;d_[k];e_[k] 1" should return the same value as "a;b;c 1", so we don't
            # add 1 to the frames count like we do in the other branch.
            frame_count_per_samples.append(kernel_split[0].count(";"))
        else:
            # no kernel frames, so e.g "a;b;c 1" and frame count is one more than ";" count.
            frame_count_per_samples.append(kernel_split[0].count(";") + 1)
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


def _collapse_stack(comm: str, stack: str, insert_dso_name: bool = False) -> str:
    """
    Collapse a single stack from "perf".
    """
    funcs = [comm]
    for line in reversed(stack.splitlines()):
        m = FRAME_REGEX.match(line)
        assert m is not None, f"bad line: {line}"
        sym, dso = m.groups()
        sym = sym.split("+")[0]  # strip the offset part.
        if sym == "[unknown]" and dso != "unknown":
            sym = f"({dso})"
        # append kernel annotation
        elif "kernel" in dso or "vmlinux" in dso:
            sym += "_[k]"
        elif insert_dso_name:
            sym += f" ({dso})"
        funcs.append(sym)
    return ";".join(funcs)


def _parse_perf_script(script: Optional[str], insert_dso_name: bool = False) -> ProcessToStackSampleCounters:
    pid_to_collapsed_stacks_counters: ProcessToStackSampleCounters = defaultdict(Counter)

    if script is None:
        return pid_to_collapsed_stacks_counters

    for sample in script.split("\n\n"):
        try:
            if sample.strip() == "":
                continue
            if sample.startswith("#"):
                continue
            match = SAMPLE_REGEX.match(sample)
            if match is None:
                raise Exception("Failed to match sample")
            sample_dict = match.groupdict()

            pid = int(sample_dict["pid"])
            comm = sample_dict["comm"]
            stack = sample_dict["stack"]
            if stack is not None:
                pid_to_collapsed_stacks_counters[pid][_collapse_stack(comm, stack, insert_dso_name)] += 1
        except Exception:
            logger.exception(f"Error processing sample: {sample}")
    return pid_to_collapsed_stacks_counters


def merge_global_perfs(
    raw_fp_perf: Optional[str], raw_dwarf_perf: Optional[str], insert_dso_name: bool = False
) -> ProcessToStackSampleCounters:
    fp_perf = _parse_perf_script(raw_fp_perf, insert_dso_name)
    dwarf_perf = _parse_perf_script(raw_dwarf_perf, insert_dso_name)

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


# TODO: automatically disable this profiler if can_i_use_perf_events() returns False?
class PerfProcess:
    _DUMP_TIMEOUT_S = 5  # timeout for waiting perf to write outputs after signaling (or right after starting)
    _RESTART_AFTER_S = 3600
    _PERF_MEMORY_USAGE_THRESHOLD = 512 * 1024 * 1024
    # default number of pages used by "perf record" when perf_event_mlock_kb=516
    # we use double for dwarf.
    _MMAP_SIZES = {"fp": 129, "dwarf": 257}

    def __init__(
        self,
        frequency: int,
        stop_event: Event,
        output_path: str,
        is_dwarf: bool,
        inject_jit: bool,
        extra_args: List[str],
        processes_to_profile: Optional[List[Process]],
        switch_timeout_s: int,
    ):
        self._start_time = 0.0
        self._frequency = frequency
        self._stop_event = stop_event
        self._output_path = output_path
        self._type = "dwarf" if is_dwarf else "fp"
        self._inject_jit = inject_jit
        self._pid_args = []
        if processes_to_profile is not None:
            self._pid_args.append("--pid")
            self._pid_args.append(",".join([str(process.pid) for process in processes_to_profile]))
        else:
            self._pid_args.append("-a")
        self._extra_args = extra_args + (["-k", "1"] if self._inject_jit else [])
        self._switch_timeout_s = switch_timeout_s
        self._process: Optional[Popen] = None

    @property
    def _log_name(self) -> str:
        return f"perf ({self._type} mode)"

    def _get_perf_cmd(self) -> List[str]:
        return (
            [
                perf_path(),
                "record",
                "-F",
                str(self._frequency),
                "-g",
                "-o",
                self._output_path,
                f"--switch-output={self._switch_timeout_s}s,signal",
                "--switch-max-files=1",
                # explicitly pass '-m', otherwise perf defaults to deriving this number from perf_event_mlock_kb,
                # and it ends up using it entirely (and we want to spare some for async-profiler)
                # this number scales linearly with the number of active cores (so we don't need to do this calculation
                # here)
                "-m",
                str(self._MMAP_SIZES[self._type]),
            ]
            + self._pid_args
            + self._extra_args
        )

    def start(self) -> None:
        logger.info(f"Starting {self._log_name}")
        # remove old files, should they exist from previous runs
        remove_path(self._output_path, missing_ok=True)
        process = start_process(self._get_perf_cmd())
        try:
            wait_event(self._DUMP_TIMEOUT_S, self._stop_event, lambda: os.path.exists(self._output_path))
            self.start_time = time.monotonic()
        except TimeoutError:
            process.kill()
            assert process.stdout is not None and process.stderr is not None
            logger.critical(
                f"{self._log_name} failed to start", stdout=process.stdout.read(), stderr=process.stderr.read()
            )
            raise
        else:
            self._process = process
            os.set_blocking(self._process.stdout.fileno(), False)  # type: ignore
            os.set_blocking(self._process.stderr.fileno(), False)  # type: ignore
            logger.info(f"Started {self._log_name}")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()  # okay to call even if process is already dead
            exit_code, stdout, stderr = reap_process(self._process)
            self._process = None
            logger.info(f"Stopped {self._log_name}", exit_code=exit_code, stderr=stderr, stdout=stdout)

    def is_running(self) -> bool:
        """
        Is perf running? returns False if perf is stopped OR if process exited since last check
        """
        return self._process is not None and self._process.poll() is None

    def restart(self) -> None:
        self.stop()
        self.start()

    def restart_if_not_running(self) -> None:
        """
        Restarts perf if it was stopped for whatever reason.
        """
        if not self.is_running():
            logger.warning(f"{self._log_name} not running (unexpectedly), restarting...")
            self.restart()

    def restart_if_rss_exceeded(self) -> None:
        """Checks if perf used memory exceeds threshold, and if it does, restarts perf"""
        assert self._process is not None
        perf_rss = Process(self._process.pid).memory_info().rss
        if (
            time.monotonic() - self._start_time >= self._RESTART_AFTER_S
            and perf_rss >= self._PERF_MEMORY_USAGE_THRESHOLD
        ):
            logger.debug(
                f"Restarting {self._log_name} due to memory exceeding limit",
                limit_rss=self._PERF_MEMORY_USAGE_THRESHOLD,
                perf_rss=perf_rss,
            )
            self.restart()

    def switch_output(self) -> None:
        assert self._process is not None, "profiling not started!"
        # clean stale files (can be emitted by perf timing out and switching output file).
        # we clean them here before sending the signal, to be able to tell between the file generated by the signal
        # to files generated by timeouts.
        remove_files_by_prefix(f"{self._output_path}.")
        self._process.send_signal(signal.SIGUSR2)

    def wait_and_script(self) -> str:
        try:
            perf_data = wait_for_file_by_prefix(f"{self._output_path}.", self._DUMP_TIMEOUT_S, self._stop_event)
        except Exception:
            assert self._process is not None and self._process.stdout is not None and self._process.stderr is not None
            logger.critical(
                f"{self._log_name} failed to dump output",
                perf_stdout=self._process.stdout.read(),
                perf_stderr=self._process.stderr.read(),
                perf_running=self.is_running(),
            )
            raise
        finally:
            # always read its stderr
            # using read1() which performs just a single read() call and doesn't read until EOF
            # (unlike Popen.communicate())
            assert self._process is not None and self._process.stderr is not None
            logger.debug(f"{self._log_name} run output", perf_stderr=self._process.stderr.read1())  # type: ignore

        try:
            inject_data = Path(f"{str(perf_data)}.inject")
            if self._inject_jit:
                run_process(
                    [perf_path(), "inject", "--jit", "-o", str(inject_data), "-i", str(perf_data)],
                )
                perf_data.unlink()
                perf_data = inject_data

            perf_script_proc = run_process(
                [perf_path(), "script", "-F", "+pid", "-i", str(perf_data)],
                suppress_log=True,
            )
            return perf_script_proc.stdout.decode("utf8")
        finally:
            perf_data.unlink()
            if self._inject_jit:
                # might be missing if it's already removed.
                # might be existing if "perf inject" itself fails
                remove_path(inject_data, missing_ok=True)


@register_profiler(
    "Perf",
    possible_modes=["fp", "dwarf", "smart", "disabled"],
    default_mode="smart",
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

        if perf_mode in ("fp", "smart"):
            self._perf_fp: Optional[PerfProcess] = PerfProcess(
                self._frequency,
                self._profiler_state.stop_event,
                os.path.join(self._profiler_state.storage_dir, "perf.fp"),
                False,
                perf_inject,
                [],
                self._profiler_state.processes_to_profile,
                switch_timeout_s,
            )
            self._perfs.append(self._perf_fp)
        else:
            self._perf_fp = None

        if perf_mode in ("dwarf", "smart"):
            self._perf_dwarf: Optional[PerfProcess] = PerfProcess(
                self._frequency,
                self._profiler_state.stop_event,
                os.path.join(self._profiler_state.storage_dir, "perf.dwarf"),
                True,
                False,  # no inject in dwarf mode, yet
                ["--call-graph", f"dwarf,{perf_dwarf_stack_size}"],
                self._profiler_state.processes_to_profile,
                switch_timeout_s,
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

    def _get_metadata(self, pid: int) -> Optional[AppMetadata]:
        if not valid_perf_pid(pid):
            return None

        try:
            process = Process(pid)
            for collector in self._metadata_collectors:
                if collector.relevant_for_process(process):
                    return collector.get_metadata(process)
        except NoSuchProcess:
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
        try:
            static = is_statically_linked(f"/proc/{process.pid}/exe")
        except FileNotFoundError:
            raise NoSuchProcess(process.pid)

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
        metadata = {"golang_version": get_process_golang_version(process)}
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
