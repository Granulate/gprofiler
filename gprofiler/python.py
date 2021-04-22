#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import concurrent.futures
import logging
import os
import signal
import glob
from pathlib import Path
from threading import Event
from typing import List, Mapping, Optional, Union

from psutil import Process

from .merge import parse_one_collapsed, parse_many_collapsed
from .exceptions import StopEventSetException, ProcessStoppedException, CalledProcessError
from .utils import pgrep_maps, start_process, poll_process, run_process, resource_path

logger = logging.getLogger(__name__)


class PythonProfilerBase:
    MAX_FREQUENCY = 100

    def __init__(self, frequency: int, duration: int, stop_event: Optional[Event], storage_dir: str):
        self._frequency = min(frequency, self.MAX_FREQUENCY)
        self._duration = duration
        self._stop_event = stop_event or Event()
        self._storage_dir = storage_dir
        logger.info(f"Initializing Python profiler (frequency: {self._frequency}hz, duration: {duration}s)")

    def start(self):
        pass

    def snapshot(self) -> Mapping[int, Mapping[str, int]]:
        """
        :returns: Mapping from pid to stacks and their counts.
        """
        raise NotImplementedError

    def stop(self):
        pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


class PySpyProfiler(PythonProfilerBase):
    MAX_FREQUENCY = 10
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

    def profile_process(self, process: Process):
        logger.info(f"Profiling process {process.pid} ({process.cmdline()})")

        local_output_path = os.path.join(self._storage_dir, f"{process.pid}.py.col.dat")
        try:
            run_process(self._make_command(process.pid, local_output_path), stop_event=self._stop_event)
        except ProcessStoppedException:
            raise StopEventSetException

        logger.info(f"Finished profiling process {process.pid} with py-spy")
        return parse_one_collapsed(Path(local_output_path).read_text())

    def find_python_processes_to_profile(self) -> List[Process]:
        filtered_procs = []
        for process in pgrep_maps(r"^.+/(?:lib)?python[^/]*$"):
            try:
                if process.pid == os.getpid():
                    continue

                cmdline = process.cmdline()
                if any(item in cmdline for item in self.BLACKLISTED_PYTHON_PROCS):
                    continue

                filtered_procs.append(process)
            except Exception:
                logger.exception(f"Couldn't add pid {process.pid} to list")

        return filtered_procs

    def snapshot(self) -> Mapping[int, Mapping[str, int]]:
        processes_to_profile = self.find_python_processes_to_profile()
        if not processes_to_profile:
            return {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=len(processes_to_profile)) as executor:
            futures = {}
            for process in processes_to_profile:
                futures[executor.submit(self.profile_process, process)] = process.pid

            results = {}
            for future in concurrent.futures.as_completed(futures):
                try:
                    results[futures[future]] = future.result()
                except StopEventSetException:
                    raise
                except Exception:
                    logger.exception(f"Failed to profile Python process {futures[future]}")

        return results


class PythonEbpfProfiler(PythonProfilerBase):
    dump_signal = signal.SIGUSR2
    poll_timeout = 10  # seconds

    def __init__(self, frequency: int, duration: int, stop_event: Optional[Event], storage_dir: str):
        super().__init__(frequency, duration, stop_event, storage_dir)
        self.process = None
        self.output_path = Path(self._storage_dir) / "py.col.dat"

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
    def _check_output(cls, process, output_path):
        if not glob.glob(f"{str(output_path)}.*"):
            stdout = process.stdout.read().decode()
            stderr = process.stderr.read().decode()
            cls._check_missing_headers(stdout)
            raise CalledProcessError(process.returncode, process.args, stdout, stderr)

    @classmethod
    def test(cls, storage_dir: str, stop_event: Optional[Event]):
        test_path = Path(storage_dir) / ".test"
        for f in glob.glob(f"{str(test_path)}.*"):
            os.unlink(f)

        # Run the process and check if the output file is properly created.
        # Wait up to 10sec for the process to terminate.
        # Allow cancellation via the stop_event.
        cmd = [resource_path("python/PyPerf"), "--output", str(test_path), "-F", "1", "--duration", "1"]
        process = start_process(cmd)
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
            resource_path("python/PyPerf"),
            "--output",
            str(self.output_path),
            "-F",
            str(self._frequency),
            # Duration is irrelevant here, we want to run continuously.
        ]
        process = start_process(cmd)
        try:
            poll_process(process, self.poll_timeout, self._stop_event)
        except TimeoutError:
            # Process is alive. So far, so good...
            if not self.output_path.exists():
                process.kill()
                stdout = process.stdout.read().decode()
                stderr = process.stderr.read().decode()
                self._check_missing_headers(stdout)
                raise CalledProcessError(process.returncode, process.args, stdout, stderr)
            # happy flow
            self.process = process
        else:
            # process terminated, must be an error:
            self._check_output(process, self.output_path)
            raise RuntimeError("_check_output did not raise!")

    def _dump(self) -> Path:
        assert self.process is not None, "profiling not started!"
        self.process.send_signal(self.dump_signal)
        # important to not grab the transient data file
        while not (output_files := glob.glob(f"{str(self.output_path)}.*")):
            if self._stop_event.wait(0.1):
                raise StopEventSetException()

        # All the snapshot samples should be in one file
        assert len(output_files) == 1
        return Path(output_files[0])

    def snapshot(self) -> Mapping[int, Mapping[str, int]]:
        if self._stop_event.wait(self._duration):
            raise StopEventSetException()
        collapsed_path = self._dump()
        collapsed_text = collapsed_path.read_text()
        collapsed_path.unlink()
        return parse_many_collapsed(collapsed_text)

    def _terminate(self) -> Optional[int]:
        code = None
        if self.process is not None:
            self.process.terminate()
            code = self.process.wait()
            self.process = None
        return code

    def stop(self):
        code = self._terminate()
        if code is not None:
            logger.info("Finished profiling Python processes with PyPerf")
        return code


_profiler_class = None


def determine_profiler_class(storage_dir: str, stop_event: Event):
    try:
        PythonEbpfProfiler.test(storage_dir, stop_event)
        return PythonEbpfProfiler
    except Exception as e:
        # Fallback to py-spy
        logger.debug(f"eBPF profiler error: {str(e)}")
        logger.info("Python eBPF profiler initialization failed. Falling back to py-spy...")
        return PySpyProfiler


def get_python_profiler(
    frequency: int, duration: int, stop_event: Event, storage_dir: str
) -> Union[PythonEbpfProfiler, PySpyProfiler]:
    global _profiler_class
    if _profiler_class is None:
        _profiler_class = determine_profiler_class(storage_dir, stop_event)
    return _profiler_class(frequency, duration, stop_event, storage_dir)
