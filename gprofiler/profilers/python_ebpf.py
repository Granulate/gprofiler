import glob
import os
import resource
import signal
from pathlib import Path
from subprocess import Popen
from threading import Event
from typing import Dict, List, NoReturn, Optional

from gprofiler.exceptions import (
        CalledProcessError,
        StopEventSetException,
)
from gprofiler.gprofiler_types import (
        ProcessToProfileData,
        ProfileData,
)
from gprofiler.profilers.profiler_base import ProfilerBase
from gprofiler.utils import (
        poll_process,
        random_prefix,
        resource_path,
        run_process,
        start_process,
        wait_event,
        wait_for_file_by_prefix,
)

class PythonEbpfError(CalledProcessError):
    """
    An error encountered while running PyPerf.
    """


class PythonEbpfProfiler(ProfilerBase):
    MAX_FREQUENCY = 1000
    PYPERF_RESOURCE = "python/pyperf/PyPerf"
    _GET_FS_OFFSET_RESOURCE = "python/pyperf/get_fs_offset"
    _GET_STACK_OFFSET_RESOURCE = "python/pyperf/get_stack_offset"
    _EVENTS_BUFFER_PAGES = 256  # 1mb and needs to be physically contiguous
    # 28mb (each symbol is 224 bytes), but needn't be physicall contiguous so don't care
    _SYMBOLS_MAP_SIZE = 131072
    _DUMP_SIGNAL = signal.SIGUSR2
    _DUMP_TIMEOUT = 5  # seconds
    _POLL_TIMEOUT = 10  # seconds
    _GET_OFFSETS_TIMEOUT = 5  # seconds

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Optional[Event],
        storage_dir: str,
        *,
        add_versions: bool,
        user_stacks_pages: Optional[int] = None,
    ):
        super().__init__(frequency, duration, stop_event, storage_dir)
        self.process: Optional[Popen] = None
        self.output_path = Path(self._storage_dir) / f"pyperf.{random_prefix()}.col"
        self.add_versions = add_versions
        self.user_stacks_pages = user_stacks_pages
        self._kernel_offsets: Dict[str, int] = {}
        self._metadata = PythonMetadata(self._stop_event)

    @classmethod
    def _pyperf_error(cls, process: Popen) -> NoReturn:
        # opened in pipe mode, so these aren't None.
        assert process.stdout is not None
        assert process.stderr is not None

        stdout = process.stdout.read().decode()
        stderr = process.stderr.read().decode()
        raise PythonEbpfError(process.returncode, process.args, stdout, stderr)

    @classmethod
    def _check_output(cls, process: Popen, output_path: Path) -> None:
        if not glob.glob(f"{str(output_path)}.*"):
            cls._pyperf_error(process)

    @staticmethod
    def _ebpf_environment() -> None:
        """
        Make sure the environment is ready so that libbpf-based programs can run.
        Technically this is needed only for container environments, but there's no reason not
        to verify those conditions stand anyway (and during our tests - we run gProfiler's executable
        in a container, so these steps have to run)
        """
        # increase memlock (Docker defaults to 64k which is not enough for the get_offset programs)
        resource.setrlimit(resource.RLIMIT_MEMLOCK, (resource.RLIM_INFINITY, resource.RLIM_INFINITY))

        # mount /sys/kernel/debug in our container
        if not os.path.ismount("/sys/kernel/debug"):
            os.makedirs("/sys/kernel/debug", exist_ok=True)
            run_process(["mount", "-t", "debugfs", "none", "/sys/kernel/debug"])

    def _get_offset(self, prog: str) -> int:
        return int(
            run_process(
                resource_path(prog), stop_event=self._stop_event, timeout=self._GET_OFFSETS_TIMEOUT
            ).stdout.strip()
        )

    def _kernel_fs_offset(self) -> int:
        try:
            return self._kernel_offsets["task_struct_fs"]
        except KeyError:
            offset = self._kernel_offsets["task_struct_fs"] = self._get_offset(self._GET_FS_OFFSET_RESOURCE)
            return offset

    def _kernel_stack_offset(self) -> int:
        try:
            return self._kernel_offsets["task_struct_stack"]
        except KeyError:
            offset = self._kernel_offsets["task_struct_stack"] = self._get_offset(self._GET_STACK_OFFSET_RESOURCE)
            return offset

    def _offset_args(self) -> List[str]:
        return [
            "--fs-offset",
            str(self._kernel_fs_offset()),
            "--stack-offset",
            str(self._kernel_stack_offset()),
        ]

    def test(self) -> None:
        self._ebpf_environment()

        for f in glob.glob(f"{str(self.output_path)}.*"):
            os.unlink(f)

        # Run the process and check if the output file is properly created.
        # Wait up to 10sec for the process to terminate.
        # Allow cancellation via the stop_event.
        cmd = [
            resource_path(self.PYPERF_RESOURCE),
            "--output",
            str(self.output_path),
            "-F",
            "1",
            "--duration",
            "1",
        ] + self._offset_args()
        process = start_process(cmd, via_staticx=True)
        try:
            poll_process(process, self._POLL_TIMEOUT, self._stop_event)
        except TimeoutError:
            process.kill()
            raise
        else:
            self._check_output(process, self.output_path)

    def start(self) -> None:
        logger.info("Starting profiling of Python processes with PyPerf")
        cmd = [
            resource_path(self.PYPERF_RESOURCE),
            "--output",
            str(self.output_path),
            "-F",
            str(self._frequency),
            "--events-buffer-pages",
            str(self._EVENTS_BUFFER_PAGES),
            "--symbols-map-size",
            str(self._SYMBOLS_MAP_SIZE),
            # Duration is irrelevant here, we want to run continuously.
        ] + self._offset_args()

        if self.user_stacks_pages is not None:
            cmd.extend(["--user-stacks-pages", str(self.user_stacks_pages)])

        for f in glob.glob(f"{str(self.output_path)}.*"):
            os.unlink(f)

        process = start_process(cmd, via_staticx=True)
        # wait until the transient data file appears - because once returning from here, PyPerf may
        # be polled via snapshot() and we need it to finish installing its signal handler.
        try:
            wait_event(self._POLL_TIMEOUT, self._stop_event, lambda: os.path.exists(self.output_path))
        except TimeoutError:
            process.kill()
            assert process.stdout is not None and process.stderr is not None
            stdout = process.stdout.read()
            stderr = process.stderr.read()
            logger.error(f"PyPerf failed to start. stdout {stdout!r} stderr {stderr!r}")
            raise
        else:
            self.process = process

    def _dump(self) -> Path:
        assert self.process is not None, "profiling not started!"
        self.process.send_signal(self._DUMP_SIGNAL)

        try:
            # important to not grab the transient data file - hence the following '.'
            output = wait_for_file_by_prefix(f"{self.output_path}.", self._DUMP_TIMEOUT, self._stop_event)
            # PyPerf outputs sampling & error counters every interval (after writing the output file), print them.
            # also, makes sure its output pipe doesn't fill up.
            # using read1() which performs just a single read() call and doesn't read until EOF
            # (unlike Popen.communicate())
            assert self.process is not None
            # Python 3.6 doesn't have read1() without size argument :/
            logger.debug(f"PyPerf output: {self.process.stderr.read1(4096)}")  # type: ignore
            return output
        except TimeoutError:
            # error flow :(
            logger.warning("PyPerf dead/not responding, killing it")
            process = self.process  # save it
            self._terminate()
            self._pyperf_error(process)

    def snapshot(self) -> ProcessToProfileData:
        if self._stop_event.wait(self._duration):
            raise StopEventSetException()
        collapsed_path = self._dump()
        try:
            collapsed_text = collapsed_path.read_text()
        finally:
            # always remove, even if we get read/decode errors
            collapsed_path.unlink()
        parsed = merge.parse_many_collapsed(collapsed_text)
        if self.add_versions:
            parsed = _add_versions_to_stacks(parsed)
        profiles = {}
        for pid in parsed:
            try:
                process = Process(pid)
                appid = application_identifiers.get_python_app_id(process)
                app_metadata = self._metadata.get_metadata(process)
            except NoSuchProcess:
                appid = None
                app_metadata = None

            profiles[pid] = ProfileData(parsed[pid], appid, app_metadata)
        return profiles

    def _terminate(self) -> Optional[int]:
        code = None
        if self.process is not None:
            self.process.terminate()  # okay to call even if process is already dead
            code = self.process.wait()
            self.process = None
        return code

    def stop(self) -> None:
        code = self._terminate()
        if code is not None:
            logger.info("Finished profiling Python processes with PyPerf")
