#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import glob
import os
import re
import resource
import signal
from collections import Counter, defaultdict
from pathlib import Path
from subprocess import Popen, TimeoutExpired
from threading import Event
from typing import Dict, List, Optional

from psutil import NoSuchProcess, Process

from gprofiler.exceptions import CalledProcessError, ProcessStoppedException, StopEventSetException
from gprofiler.gprofiler_types import ProcessToStackSampleCounters, StackToSampleCount, nonnegative_integer
from gprofiler.log import get_logger_adapter
from gprofiler.merge import parse_many_collapsed, parse_one_collapsed_file
from gprofiler.metadata.py_package_version import get_modules_versions
from gprofiler.metadata.system_metadata import get_arch
from gprofiler.profilers.profiler_base import ProcessProfilerBase, ProfilerBase, ProfilerInterface
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import (
    is_process_running,
    pgrep_maps,
    poll_process,
    process_comm,
    random_prefix,
    removed_path,
    resource_path,
    run_process,
    start_process,
    wait_event,
    wait_for_file_by_prefix,
)

logger = get_logger_adapter(__name__)

_module_name_in_stack = re.compile(r"\((?P<module_info>(?P<filename>.+?\.py):\d+)\)")


def _add_versions_to_process_stacks(pid: int, stacks: StackToSampleCount) -> StackToSampleCount:
    # TODO: Optimize the whole thing. Specifically, add cache in get_modules_versions
    new_stacks: StackToSampleCount = Counter()
    for stack in stacks:
        modules_paths = (match.group("filename") for match in _module_name_in_stack.finditer(stack))
        packages_versions = get_modules_versions(modules_paths, pid)

        def _replace_module_name(module_name_match):
            package_info = packages_versions.get(module_name_match.group("filename"))
            if package_info is not None:
                package_name, package_version = package_info
                return "({} ({}-{}))".format(module_name_match.group("module_info"), package_name, package_version)
            return module_name_match.group()

        new_stack = _module_name_in_stack.sub(_replace_module_name, stack)
        new_stacks[new_stack] = stacks[stack]

    return new_stacks


def _add_versions_to_stacks(
    process_to_stack_sample_counters: ProcessToStackSampleCounters,
) -> ProcessToStackSampleCounters:
    result: ProcessToStackSampleCounters = defaultdict(Counter)

    for pid, stack_to_sample_count in process_to_stack_sample_counters.items():
        result[pid] = _add_versions_to_process_stacks(pid, stack_to_sample_count)

    return result


class PySpyProfiler(ProcessProfilerBase):
    MAX_FREQUENCY = 50
    _BLACKLISTED_PYTHON_PROCS = ["unattended-upgrades", "networkd-dispatcher", "supervisord", "tuned"]
    _EXTRA_TIMEOUT = 10  # give py-spy some seconds to run (added to the duration)

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Optional[Event],
        storage_dir: str,
        add_versions: bool,
    ):
        super().__init__(frequency, duration, stop_event, storage_dir)
        self.add_versions = add_versions

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

    def _profile_process(self, process: Process) -> Optional[StackToSampleCount]:
        try:
            logger.info(
                f"Profiling process {process.pid} with py-spy", cmdline=process.cmdline(), no_extra_to_server=True
            )
        except NoSuchProcess:
            return None

        local_output_path = os.path.join(self._storage_dir, f"pyspy.{random_prefix()}.{process.pid}.col")
        with removed_path(local_output_path):
            try:
                run_process(
                    self._make_command(process.pid, local_output_path),
                    stop_event=self._stop_event,
                    timeout=self._duration + self._EXTRA_TIMEOUT,
                    kill_signal=signal.SIGKILL,
                )
            except ProcessStoppedException:
                raise StopEventSetException
            except TimeoutExpired:
                logger.error(f"Profiling with py-spy timed out on process {process.pid}")
                raise
            except CalledProcessError as e:
                if (
                    b"Error: Failed to get process executable name. Check that the process is running.\n" in e.stderr
                    and not is_process_running(process)
                ):
                    logger.debug(f"Profiled process {process.pid} exited before py-spy could start")
                    return None
                raise

            logger.info(f"Finished profiling process {process.pid} with py-spy")
            parsed = parse_one_collapsed_file(Path(local_output_path), process_comm(process))
            if self.add_versions:
                parsed = _add_versions_to_process_stacks(process.pid, parsed)
            return parsed

    def _select_processes_to_profile(self) -> List[Process]:
        filtered_procs = []
        for process in pgrep_maps(
            r"(?:^.+/(?:lib)?python[^/]*$)|(?:^.+/site-packages/.+?$)|(?:^.+/dist-packages/.+?$)"
        ):
            try:
                if process.pid == os.getpid():
                    continue

                cmdline = process.cmdline()
                if any(item in cmdline for item in self._BLACKLISTED_PYTHON_PROCS):
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


class PythonEbpfError(CalledProcessError):
    """
    An error encountered while running PyPerf.
    """


class PythonEbpfProfiler(ProfilerBase):
    MAX_FREQUENCY = 1000
    PYPERF_RESOURCE = "python/pyperf/PyPerf"
    _GET_FS_OFFSET_RESOURCE = "python/pyperf/get_fs_offset"
    _GET_STACK_OFFSET_RESOURCE = "python/pyperf/get_stack_offset"
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
        add_versions: bool,
        user_stacks_pages: Optional[int] = None,
    ):
        super().__init__(frequency, duration, stop_event, storage_dir)
        self.process = None
        self.output_path = Path(self._storage_dir) / f"pyperf.{random_prefix()}.col"
        self.add_versions = add_versions
        self.user_stacks_pages = user_stacks_pages
        self._kernel_offsets: Dict[str, int] = {}

    @classmethod
    def _pyperf_error(cls, process: Popen):
        # opened in pipe mode, so these aren't None.
        assert process.stdout is not None
        assert process.stderr is not None

        stdout = process.stdout.read().decode()
        stderr = process.stderr.read().decode()
        raise PythonEbpfError(process.returncode, process.args, stdout, stderr)

    @classmethod
    def _check_output(cls, process: Popen, output_path: Path):
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
        return int(run_process(resource_path(prog)).stdout.strip())

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
            poll_process(process, self.poll_timeout, self._stop_event)
        except TimeoutError:
            process.kill()
            raise
        else:
            self._check_output(process, self.output_path)

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
        ] + self._offset_args()

        if self.user_stacks_pages is not None:
            cmd.extend(["--user-stacks-pages", str(self.user_stacks_pages)])

        for f in glob.glob(f"{str(self.output_path)}.*"):
            os.unlink(f)

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

    def _dump(self) -> Path:
        assert self.process is not None, "profiling not started!"
        self.process.send_signal(self.dump_signal)

        try:
            # important to not grab the transient data file - hence the following '.'
            output = wait_for_file_by_prefix(f"{self.output_path}.", self.dump_timeout, self._stop_event)
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
        try:
            collapsed_text = collapsed_path.read_text()
        finally:
            # always remove, even if we get read/decode errors
            collapsed_path.unlink()
        parsed = parse_many_collapsed(collapsed_text)
        if self.add_versions:
            parsed = _add_versions_to_stacks(parsed)
        return parsed

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


@register_profiler(
    "Python",
    # py-spy is like pyspy, it's confusing and I mix between them
    possible_modes=["auto", "pyperf", "pyspy", "py-spy", "disabled"],
    default_mode="auto",
    # we build pyspy for both, pyperf only for x86_64.
    # TODO: this inconsistency shows that py-spy and pyperf should have different Profiler classes,
    # we should split them in the future.
    supported_archs=["x86_64", "aarch64"],
    profiler_mode_argument_help="Select the Python profiling mode: auto (try PyPerf, resort to py-spy if it fails), "
    "pyspy (always use py-spy), pyperf (always use PyPerf, and avoid py-spy even if it fails)"
    " or disabled (no runtime profilers for Python).",
    profiler_arguments=[
        ProfilerArgument(
            "--no-python-versions",
            dest="python_add_versions",
            action="store_false",
            help="Don't add version information to Python frames. If not set, frames from packages are displayed with "
            "the name of the package and its version, and frames from Python built-in modules are displayed with "
            "Python's full version.",
        ),
        ProfilerArgument(
            "--pyperf-user-stacks-pages",
            dest="python_pyperf_user_stacks_pages",
            default=None,
            type=nonnegative_integer,
            help="Number of user stack-pages that PyPerf will collect, this controls the maximum stack depth of native "
            "user frames. Pass 0 to disable user native stacks altogether.",
        ),
    ],
)
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
        python_add_versions: bool,
        python_pyperf_user_stacks_pages: Optional[int],
    ):
        if python_mode == "py-spy":
            python_mode = "pyspy"

        assert python_mode in ("auto", "pyperf", "pyspy"), f"unexpected mode: {python_mode}"

        if get_arch() != "x86_64":
            if python_mode == "pyperf":
                logger.warning("PyPerf is supported only on x86_64, falling back to py-spy")
            python_mode = "pyspy"

        if python_mode in ("auto", "pyperf"):
            self._ebpf_profiler = self._create_ebpf_profiler(
                frequency, duration, stop_event, storage_dir, python_add_versions, python_pyperf_user_stacks_pages
            )
        else:
            self._ebpf_profiler = None

        if python_mode in ("auto", "pyspy"):
            self._pyspy_profiler: Optional[PySpyProfiler] = PySpyProfiler(
                frequency, duration, stop_event, storage_dir, python_add_versions
            )
        else:
            self._pyspy_profiler = None

    def _create_ebpf_profiler(
        self,
        frequency: int,
        duration: int,
        stop_event: Event,
        storage_dir: str,
        add_versions: bool,
        user_stacks_pages: Optional[int],
    ) -> Optional[PythonEbpfProfiler]:
        try:
            profiler = PythonEbpfProfiler(frequency, duration, stop_event, storage_dir, add_versions, user_stacks_pages)
            profiler.test()
            return profiler
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
