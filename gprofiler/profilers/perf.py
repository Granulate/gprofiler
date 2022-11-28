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

from granulate_utils.golang import get_process_golang_version, is_golang_process
from granulate_utils.linux.elf import is_statically_linked
from granulate_utils.linux.process import is_musl, is_process_running
from granulate_utils.node import is_node_process
from psutil import NoSuchProcess, Process

from gprofiler import merge
from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import AppMetadata, ProcessToProfileData, ProfileData, StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import application_identifiers
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.profilers.node import clean_up_node_maps, generate_map_for_node_processes, get_node_processes
from gprofiler.profilers.profiler_base import ProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import run_process, start_process, wait_event, wait_for_file_by_prefix
from gprofiler.utils.perf import perf_path

logger = get_logger_adapter(__name__)

DEFAULT_PERF_DWARF_STACK_SIZE = 8192


# TODO: automatically disable this profiler if can_i_use_perf_events() returns False?
class PerfProcess:
    _dump_timeout_s = 5
    _poll_timeout_s = 5
    # default number of pages used by "perf record" when perf_event_mlock_kb=516
    # we use double for dwarf.
    _mmap_sizes = {"fp": 129, "dwarf": 257}

    def __init__(
        self,
        frequency: int,
        stop_event: Event,
        output_path: str,
        is_dwarf: bool,
        inject_jit: bool,
        extra_args: List[str],
    ):
        self._frequency = frequency
        self._stop_event = stop_event
        self._output_path = output_path
        self._type = "dwarf" if is_dwarf else "fp"
        self._inject_jit = inject_jit
        self._extra_args = extra_args + (["-k", "1"] if self._inject_jit else [])
        self._process: Optional[Popen] = None

    def _get_perf_cmd(self) -> List[str]:
        return [
            perf_path(),
            "record",
            "-F",
            str(self._frequency),
            "-a",
            "-g",
            "-o",
            self._output_path,
            "--switch-output=signal",
            # explicitly pass '-m', otherwise perf defaults to deriving this number from perf_event_mlock_kb,
            # and it ends up using it entirely (and we want to spare some for async-profiler)
            # this number scales linearly with the number of active cores (so we don't need to do this calculation
            # here)
            "-m",
            str(self._mmap_sizes[self._type]),
        ] + self._extra_args

    def start(self) -> None:
        logger.info(f"Starting perf ({self._type} mode)")
        process = start_process(self._get_perf_cmd(), via_staticx=False)
        try:
            wait_event(self._poll_timeout_s, self._stop_event, lambda: os.path.exists(self._output_path))
        except TimeoutError:
            process.kill()
            assert process.stdout is not None and process.stderr is not None
            logger.critical(f"perf failed to start. stdout {process.stdout.read()!r} stderr {process.stderr.read()!r}")
            raise
        else:
            self._process = process
            logger.info(f"Started perf ({self._type} mode)")

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()  # okay to call even if process is already dead
            self._process.wait()
            self._process = None
            logger.info(f"Stopped perf ({self._type} mode)")

    def switch_output(self) -> None:
        assert self._process is not None, "profiling not started!"
        self._process.send_signal(signal.SIGUSR2)

    def wait_and_script(self) -> str:
        try:
            perf_data = wait_for_file_by_prefix(f"{self._output_path}.", self._dump_timeout_s, self._stop_event)
        except Exception:
            assert self._process is not None and self._process.stdout is not None and self._process.stderr is not None
            logger.critical(
                f"perf failed to dump output. stdout {self._process.stdout.read()!r}"
                f" stderr {self._process.stderr.read()!r}"
            )
            raise
        finally:
            # always read its stderr
            # using read1() which performs just a single read() call and doesn't read until EOF
            # (unlike Popen.communicate())
            assert self._process is not None and self._process.stderr is not None
            logger.debug(f"perf stderr: {self._process.stderr.read1(4096)}")  # type: ignore

        if self._inject_jit:
            inject_data = Path(f"{str(perf_data)}.inject")
            run_process(
                [perf_path(), "inject", "--jit", "-o", str(inject_data), "-i", str(perf_data)],
            )
            perf_data.unlink()
            perf_data = inject_data

        perf_script_proc = run_process(
            [perf_path(), "script", "-F", "+pid", "-i", str(perf_data)],
            suppress_log=True,
        )
        perf_data.unlink()
        return perf_script_proc.stdout.decode("utf8")


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
        )
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
        stop_event: Event,
        storage_dir: str,
        insert_dso_name: bool,
        profiling_mode: str,
        profile_spawned_processes: bool,
        perf_mode: str,
        perf_dwarf_stack_size: int,
        perf_inject: bool,
        perf_node_attach: bool,
    ):
        super().__init__(frequency, duration, stop_event, storage_dir, insert_dso_name, profiling_mode)
        _ = profile_spawned_processes  # Required for mypy unused argument warning
        self._perfs: List[PerfProcess] = []
        self._metadata_collectors: List[PerfMetadata] = [GolangPerfMetadata(stop_event), NodePerfMetadata(stop_event)]
        self._insert_dso_name = insert_dso_name
        self._node_processes: List[Process] = []
        self._node_processes_attached: List[Process] = []

        if perf_mode in ("fp", "smart"):
            self._perf_fp: Optional[PerfProcess] = PerfProcess(
                self._frequency,
                self._stop_event,
                os.path.join(self._storage_dir, "perf.fp"),
                False,
                perf_inject,
                [],
            )
            self._perfs.append(self._perf_fp)
        else:
            self._perf_fp = None

        if perf_mode in ("dwarf", "smart"):
            self._perf_dwarf: Optional[PerfProcess] = PerfProcess(
                self._frequency,
                self._stop_event,
                os.path.join(self._storage_dir, "perf.dwarf"),
                True,
                False,  # no inject in dwarf mode, yet
                ["--call-graph", f"dwarf,{perf_dwarf_stack_size}"],
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
        if pid in (0, -1):  # funny values retrieved by perf
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

        if self._stop_event.wait(self._duration):
            raise StopEventSetException

        for perf in self._perfs:
            perf.switch_output()

        return {
            k: self._generate_profile_data(v, k)
            for k, v in merge.merge_global_perfs(
                self._perf_fp.wait_and_script() if self._perf_fp is not None else None,
                self._perf_dwarf.wait_and_script() if self._perf_dwarf is not None else None,
                self._insert_dso_name,
            ).items()
        }

    def _generate_profile_data(self, stacks: StackToSampleCount, pid: int) -> ProfileData:
        metadata = self._get_metadata(pid)
        if metadata is not None and "node_version" in metadata:
            appid = self._get_appid(pid)
        else:
            appid = None
        return ProfileData(stacks, appid, metadata)


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
