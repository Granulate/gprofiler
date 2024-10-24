import os
import signal
import time
from pathlib import Path
from subprocess import Popen
from threading import Event
from typing import List, Optional

from psutil import Process

from gprofiler.log import get_logger_adapter
from gprofiler.utils import (
    reap_process,
    remove_files_by_prefix,
    remove_path,
    resource_path,
    run_process,
    start_process,
    wait_event,
    wait_for_file_by_prefix,
)

logger = get_logger_adapter(__name__)


def perf_path() -> str:
    return resource_path("perf")


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
        *,
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
        self._extra_args = extra_args
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
            + (["-k", "1"] if self._inject_jit else [])
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
                [perf_path(), "script", "-F", "+pid,+symline", "-i", str(perf_data)],
                suppress_log=True,
            )
            return perf_script_proc.stdout.decode("utf8")
        finally:
            perf_data.unlink()
            if self._inject_jit:
                # might be missing if it's already removed.
                # might be existing if "perf inject" itself fails
                remove_path(inject_data, missing_ok=True)
