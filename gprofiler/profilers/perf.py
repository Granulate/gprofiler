#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import signal
from pathlib import Path
from threading import Event
from typing import List, Optional

import psutil

from gprofiler.exceptions import StopEventSetException
from gprofiler.log import get_logger_adapter
from gprofiler.merge import ProcessToStackSampleCounters, merge_global_perfs
from gprofiler.profilers.profiler_base import ProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import resource_path, run_process, start_process, wait_event, wait_for_file_by_prefix

logger = get_logger_adapter(__name__)


def perf_path() -> str:
    return resource_path("perf")


class PerfProcess:
    _dump_timeout_s = 5
    _poll_timeout_s = 5

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
        self._process: Optional[psutil.Process] = None

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
        ] + self._extra_args

    def start(self) -> None:
        logger.info(f"Starting perf ({self._type} mode)")
        process = start_process(self._get_perf_cmd(), via_staticx=False)
        try:
            wait_event(self._poll_timeout_s, self._stop_event, lambda: os.path.exists(self._output_path))
        except TimeoutError:
            process.kill()
            assert process.stdout is not None and process.stderr is not None
            logger.error(f"perf failed to start. stdout {process.stdout.read()!r} stderr {process.stderr.read()!r}")
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
        perf_data = wait_for_file_by_prefix(f"{self._output_path}.", self._dump_timeout_s, self._stop_event)

        # using read1() which performs just a single read() call and doesn't read until EOF
        # (unlike Popen.communicate())
        assert self._process is not None
        logger.debug(f"perf stderr: {self._process.stderr.read1(4096)}")

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
        return perf_script_proc.stdout.decode('utf8')


@register_profiler(
    "Perf",
    possible_modes=["fp", "dwarf", "smart", "disabled"],
    default_mode="fp",
    profiler_mode_argument_help="Run perf with either FP (Frame Pointers), DWARF, or run both and intelligently merge"
    " them by choosing the best result per process. If 'disabled' is chosen, do not invoke"
    " 'perf' at all. The output, in that case, is the concatenation of the results from all"
    " of the runtime profilers.",
    profiler_arguments=[
        ProfilerArgument(
            "--perf-dwarf-stack-size",
            help="The max stack size for the Dwarf perf, in bytes. Must be <=65528."
            " Relevant for --perf-mode dwarf|smart. Default: %(default)s",
            type=int,
            default=8192,
            dest="perf_dwarf_stack_size",
        )
    ],
    disablement_help="Disable the global perf of processes,"
    " and instead only concatenate runtime-specific profilers results",
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
        perf_mode: str,
        perf_dwarf_stack_size: int,
        perf_nodejs_mode: str,
    ):
        super().__init__(frequency, duration, stop_event, storage_dir)
        self._perfs: List[PerfProcess] = []

        if perf_mode in ("fp", "smart"):
            self._perf_fp: Optional[PerfProcess] = PerfProcess(
                self._frequency,
                self._stop_event,
                os.path.join(self._storage_dir, "perf.fp"),
                False,
                perf_nodejs_mode == "perf",
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

        assert self._perf_fp is not None or self._perf_dwarf is not None

    def start(self) -> None:
        for perf in self._perfs:
            perf.start()

    def stop(self) -> None:
        for perf in reversed(self._perfs):
            perf.stop()

    def snapshot(self) -> ProcessToStackSampleCounters:
        if self._stop_event.wait(self._duration):
            raise StopEventSetException

        for perf in self._perfs:
            perf.switch_output()

        return merge_global_perfs(
            self._perf_fp.wait_and_script() if self._perf_fp is not None else None,
            self._perf_dwarf.wait_and_script() if self._perf_dwarf is not None else None,
        )
