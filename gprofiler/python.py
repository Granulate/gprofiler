#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import concurrent.futures
import glob
import logging
import os
import signal
from pathlib import Path
from subprocess import Popen
from threading import Event
from typing import List, Optional

from psutil import Process

from gprofiler.exceptions import CalledProcessError, ProcessStoppedException, StopEventSetException
from gprofiler.merge import parse_many_collapsed, parse_one_collapsed
from gprofiler.profiler_base import ProfilerBase, ProfilerInterface
from gprofiler.types import ProcessToStackSampleCounters
from gprofiler.utils import (
    pgrep_maps,
    poll_process,
    random_prefix,
    resource_path,
    run_process,
    start_process,
    wait_event,
)

logger = logging.getLogger(__name__)


class PySpyProfiler(ProfilerBase):
    MAX_FREQUENCY = 50
    BLACKLISTED_PYTHON_PROCS = ["unattended-upgrades", "networkd-dispatcher", "supervisord", "tuned"]

    def _make_command(self, pid: int, output_path: str):
        return [
            resource_path("python/py-spy"),
            "record",
            "-r",
            str(self._frequency),
            "-d",
            str(self._duration),
            "--nonblocking",
            "--format",
            "raw",
            "-F",
            "--gil",
            "--output",
            output_path,
            "-p",
            str(pid),
            "--full-filenames",
        ]

    def _profile_process(self, process: Process):
        logger.info(f"Profiling process {process.pid} ({process.cmdline()})")
        comm = process.name()

        local_output_path = os.path.join(self._storage_dir, f"pyspy.{random_prefix()}.{process.pid}.col")
        try:
            run_process(self._make_command(process.pid, local_output_path), stop_event=self._stop_event)
        except ProcessStoppedException:
            raise StopEventSetException

        logger.info(f"Finished profiling process {process.pid} with py-spy")
        return parse_one_collapsed(Path(local_output_path).read_text(), comm)

    def _find_python_processes_to_profile(self) -> List[Process]:
        filtered_procs = []
        for process in pgrep_maps(
            r"(?:^.+/(?:lib)?python[^/]*$)|(?:^.+/site-packages/.+?$)|(?:^.+/dist-packages/.+?$)"
        ):
            try:
                if process.pid == os.getpid():
                    continue

                cmdline = process.cmdline()
                if any(item in cmdline for item in self.BLACKLISTED_PYTHON_PROCS):
                    continue

                # PyPy is called pypy3 or pypy (for 2)
                # py-spy is, of course, only for CPython, and will report a possibly not-so-nice error
                # when invoked on pypy.
                # I'm checking for "pypy" in the basename here. I'm not aware of libpypy being directly loaded
                # into non-pypy processes, if we ever encounter that - we can check the maps instead
                if os.path.basename(process.exe()).startswith("pypy"):
                    continue

                filtered_procs.append(process)
            except Exception:
                logger.exception(f"Couldn't add pid {process.pid} to list")

        return filtered_procs

    def snapshot(self) -> ProcessToStackSampleCounters:
        processes_to_profile = self._find_python_processes_to_profile()
        if not processes_to_profile:
            return {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(processes_to_profile)) as executor:
            futures = {}
            for process in processes_to_profile:
                futures[executor.submit(self._profile_process, process)] = process.pid

            results = {}
            for future in concurrent.futures.as_completed(futures):
                try:
                    results[futures[future]] = future.result()
                except StopEventSetException:
                    raise
                except Exception:
                    logger.exception(f"Failed to profile Python process {futures[future]}")

        return results


class PythonEbpfError(CalledProcessError):
    """
    An error encountered while running PyPerf.
    """


class PythonEbpfProfiler(ProfilerBase):
    MAX_FREQUENCY = 1000
    PYPERF_RESOURCE = "python/pyperf/PyPerf"
    events_buffer_pages = 256  # 1mb and needs to be physically contiguous
    # 28mb (each symbol is 224 bytes), but needn't be physicall contiguous so don't care
    symbols_map_size = 131072
    dump_signal = signal.SIGUSR2
    dump_timeout = 5  # seconds
    poll_timeout = 10  # seconds

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Optional[Event],
        storage_dir: str,
    ):
        super().__init__(frequency, duration, stop_event, storage_dir)
        self.process = None
        self.output_path = Path(self._storage_dir) / f"pyperf.{random_prefix()}.col"

    @classmethod
    def _check_missing_headers(cls, stdout) -> bool:
        if "Unable to find kernel headers." in stdout:
            print()
            print("Unable to find kernel headers. Make sure the package is installed for your distribution.")
            print("If you are using Ubuntu, you can install the required package using:")
            print()
            print("    apt install linux-headers-$(uname -r)")
            print()
            print("If you are still getting this error and you are running gProfiler as a docker container,")
            print("make sure /lib/modules and /usr/src are mapped into the container.")
            print("See the README for further details.")
            print()
            return True
        else:
            return False

    @classmethod
    def _pyperf_error(cls, process: Popen):
        # opened in pipe mode, so these aren't None.
        assert process.stdout is not None
        assert process.stderr is not None

        stdout = process.stdout.read().decode()
        stderr = process.stderr.read().decode()
        cls._check_missing_headers(stdout)
        raise PythonEbpfError(process.returncode, process.args, stdout, stderr)

    @classmethod
    def _check_output(cls, process: Popen, output_path: Path):
        if not glob.glob(f"{str(output_path)}.*"):
            cls._pyperf_error(process)

    @classmethod
    def test(cls, storage_dir: str, stop_event: Event):
        test_path = Path(storage_dir) / ".test"
        for f in glob.glob(f"{str(test_path)}.*"):
            os.unlink(f)

        # Run the process and check if the output file is properly created.
        # Wait up to 10sec for the process to terminate.
        # Allow cancellation via the stop_event.
        cmd = [resource_path(cls.PYPERF_RESOURCE), "--output", str(test_path), "-F", "1", "--duration", "1"]
        process = start_process(cmd, via_staticx=True)
        try:
            poll_process(process, cls.poll_timeout, stop_event)
        except TimeoutError:
            process.kill()
            raise
        else:
            cls._check_output(process, test_path)

    def start(self):
        logger.info("Starting profiling of Python processes with PyPerf")
        cmd = [
            resource_path(self.PYPERF_RESOURCE),
            "--output",
            str(self.output_path),
            "-F",
            str(self._frequency),
            "--events-buffer-pages",
            str(self.events_buffer_pages),
            "--symbols-map-size",
            str(self.symbols_map_size),
            # Duration is irrelevant here, we want to run continuously.
        ]
        process = start_process(cmd, via_staticx=True)
        # wait until the transient data file appears - because once returning from here, PyPerf may
        # be polled via snapshot() and we need it to finish installing its signal handler.
        try:
            wait_event(self.poll_timeout, self._stop_event, lambda: os.path.exists(self.output_path))
        except TimeoutError:
            process.kill()
            logger.error(f"PyPerf failed to start. stdout {process.stdout.read()!r} stderr {process.stderr.read()!r}")
            raise
        else:
            self.process = process

    def _glob_output(self) -> List[str]:
        # important to not grab the transient data file
        return glob.glob(f"{str(self.output_path)}.*")

    def _wait_for_output_file(self, timeout: float) -> Path:
        wait_event(timeout, self._stop_event, lambda: len(self._glob_output()) > 0)

        output_files = self._glob_output()
        # All the snapshot samples should be in one file
        assert len(output_files) == 1
        return Path(output_files[0])

    def _dump(self) -> Path:
        assert self.process is not None, "profiling not started!"
        self.process.send_signal(self.dump_signal)

        try:
            output = self._wait_for_output_file(self.dump_timeout)
            # PyPerf outputs sampling & error counters every interval (after writing the output file), print them.
            # also, makes sure its output pipe doesn't fill up.
            # using read1() which performs just a single read() call and doesn't read until EOF
            # (unlike Popen.communicate())
            assert self.process is not None
            # Python 3.6 doesn't have read1() without size argument :/
            logger.debug(f"PyPerf output: {self.process.stderr.read1(4096)}")
            return output
        except TimeoutError:
            # error flow :(
            logger.warning("PyPerf dead/not responding, killing it")
            process = self.process  # save it
            self._terminate()
            self._pyperf_error(process)

    def snapshot(self) -> ProcessToStackSampleCounters:
        if self._stop_event.wait(self._duration):
            raise StopEventSetException()
        collapsed_path = self._dump()
        collapsed_text = collapsed_path.read_text()
        collapsed_path.unlink()
        return parse_many_collapsed(collapsed_text)

    def _terminate(self) -> Optional[int]:
        code = None
        if self.process is not None:
            self.process.terminate()  # okay to call even if process is already dead
            code = self.process.wait()
            self.process = None
        return code

    def stop(self):
        code = self._terminate()
        if code is not None:
            logger.info("Finished profiling Python processes with PyPerf")


class PythonProfiler(ProfilerInterface):
    """
    Controls PySpyProfiler & PythonEbpfProfiler as needed, providing a clean interface
    to GProfiler.
    """

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Event,
        storage_dir: str,
        python_mode: str,
    ):
        # not calling my c'tor - we're just a proxy class
        assert python_mode in ("auto", "pyperf", "pyspy"), f"unexpected mode: {python_mode}"
        if python_mode in ("auto", "pyperf"):
            self._ebpf_profiler = self._create_ebpf_profiler(frequency, duration, stop_event, storage_dir)
        else:
            self._ebpf_profiler = None

        if python_mode in ("auto", "pyspy"):
            self._pyspy_profiler: Optional[PySpyProfiler] = PySpyProfiler(frequency, duration, stop_event, storage_dir)
        else:
            self._pyspy_profiler = None

    def _create_ebpf_profiler(
        self, frequency: int, duration: int, stop_event: Event, storage_dir: str
    ) -> Optional[PythonEbpfProfiler]:
        try:
            PythonEbpfProfiler.test(storage_dir, stop_event)
            return PythonEbpfProfiler(frequency, duration, stop_event, storage_dir)
        except Exception as e:
            logger.debug(f"eBPF profiler error: {str(e)}")
            logger.info("Python eBPF profiler initialization failed")
            return None

    def start(self) -> None:
        if self._ebpf_profiler is not None:
            self._ebpf_profiler.start()
        elif self._pyspy_profiler is not None:
            self._pyspy_profiler.start()

    def snapshot(self) -> ProcessToStackSampleCounters:
        if self._ebpf_profiler is not None:
            try:
                return self._ebpf_profiler.snapshot()
            except PythonEbpfError as e:
                pypspy_msg = ", falling back to py-spy" if self._pyspy_profiler is not None else ""
                logger.warning(f"Python eBPF profiler failed (exit code: {e.returncode}){pypspy_msg}")
                self._ebpf_profiler = None
                return {}  # empty this round
        elif self._pyspy_profiler is not None:
            return self._pyspy_profiler.snapshot()
        else:
            # this can happen python_mode was 'pyperf' and PyPerf has failed.
            # we won't resort to py-spy in this case.
            return {}

    def stop(self) -> None:
        if self._ebpf_profiler is not None:
            self._ebpf_profiler.stop()
        elif self._pyspy_profiler is not None:
            self._pyspy_profiler.stop()
