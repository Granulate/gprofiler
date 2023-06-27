#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import re
import signal
from collections import Counter, defaultdict
from pathlib import Path
from subprocess import CompletedProcess
from typing import Any, Dict, List, Match, Optional, cast

from granulate_utils.linux.elf import get_elf_id
from granulate_utils.linux.ns import get_process_nspid, run_in_ns
from granulate_utils.linux.process import (
    get_mapped_dso_elf_id,
    is_process_basename_matching,
    is_process_running,
    process_exe,
)
from granulate_utils.python import _BLACKLISTED_PYTHON_PROCS, DETECTED_PYTHON_PROCESSES_REGEX
from psutil import NoSuchProcess, Process

from gprofiler.exceptions import (
    CalledProcessError,
    CalledProcessTimeoutError,
    ProcessStoppedException,
    StopEventSetException,
)
from gprofiler.gprofiler_types import ProcessToStackSampleCounters, ProfileData, StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import application_identifiers
from gprofiler.metadata.application_metadata import ApplicationMetadata
from gprofiler.metadata.py_module_version import get_modules_versions
from gprofiler.platform import is_linux, is_windows
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.profiler_base import SpawningProcessProfilerBase
from gprofiler.profilers.registry import register_profiler
from gprofiler.utils import pgrep_exe, pgrep_maps, random_prefix, removed_path, resource_path, run_process
from gprofiler.utils.collapsed_format import parse_one_collapsed_file
from gprofiler.utils.process import process_comm, search_proc_maps

logger = get_logger_adapter(__name__)

_module_name_in_stack = re.compile(r"\((?P<module_info>(?P<filename>[^\)]+?\.py):\d+)\)")


def _add_versions_to_process_stacks(process: Process, stacks: StackToSampleCount) -> StackToSampleCount:
    new_stacks: StackToSampleCount = Counter()
    for stack in stacks:
        modules_paths = (match.group("filename") for match in _module_name_in_stack.finditer(stack))
        packages_versions = get_modules_versions(modules_paths, process)

        def _replace_module_name(module_name_match: Match) -> str:
            package_info = packages_versions.get(module_name_match.group("filename"))
            if package_info is not None:
                package_name, package_version = package_info
                return "({} [{}=={}])".format(module_name_match.group("module_info"), package_name, package_version)
            return cast(str, module_name_match.group())

        new_stack = _module_name_in_stack.sub(_replace_module_name, stack)
        new_stacks[new_stack] = stacks[stack]

    return new_stacks


def _add_versions_to_stacks(
    process_to_stack_sample_counters: ProcessToStackSampleCounters,
) -> ProcessToStackSampleCounters:
    result: ProcessToStackSampleCounters = defaultdict(Counter)

    for pid, stack_to_sample_count in process_to_stack_sample_counters.items():
        try:
            process = Process(pid)
        except NoSuchProcess:
            # The process doesn't exist anymore so we can't analyze versions
            continue
        result[pid] = _add_versions_to_process_stacks(process, stack_to_sample_count)

    return result


class PythonMetadata(ApplicationMetadata):
    _PYTHON_TIMEOUT = 3

    def _get_python_version(self, process: Process) -> Optional[str]:
        try:
            if is_process_basename_matching(process, application_identifiers._PYTHON_BIN_RE):
                version_arg = "-V"
                prefix = ""
            elif is_process_basename_matching(process, r"^uwsgi$"):
                version_arg = "--python-version"
                # for compatibility, we add this prefix (to match python -V)
                prefix = "Python "
            else:
                # TODO: for dynamic executables, find the python binary that works with the loaded libpython, and
                # check it instead. For static executables embedding libpython - :shrug:
                raise NotImplementedError

            # Python 2 prints -V to stderr, so try that as well.
            return prefix + self.get_exe_version_cached(process, version_arg=version_arg, try_stderr=True)
        except Exception:
            return None

    def _get_sys_maxunicode(self, process: Process) -> Optional[str]:
        try:
            if not is_process_basename_matching(process, application_identifiers._PYTHON_BIN_RE):
                # see same raise above
                raise NotImplementedError

            python_path = f"/proc/{get_process_nspid(process.pid)}/exe"

            def _run_python_process_in_ns() -> "CompletedProcess[bytes]":
                return run_process(
                    [python_path, "-S", "-c", "import sys; print(sys.maxunicode)"],
                    stop_event=self._stop_event,
                    timeout=self._PYTHON_TIMEOUT,
                )

            return run_in_ns(["pid", "mnt"], _run_python_process_in_ns, process.pid).stdout.decode().strip()
        except Exception:
            return None

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        # python version
        version = self._get_python_version(process)

        # if python 2 - collect sys.maxunicode as well, to differentiate between ucs2 and ucs4
        if version is not None and version.startswith("Python 2."):
            maxunicode: Optional[str] = self._get_sys_maxunicode(process)
        else:
            maxunicode = None

        # python id & libpython id, if exists.
        # if libpython exists then the python binary itself is of less importance; however, to avoid confusion
        # we collect them both here (then we're able to know if either exist)
        if is_windows():
            exe_elfid = None
            libpython_elfid = None
        else:
            exe_elfid = get_elf_id(f"/proc/{process.pid}/exe")
            libpython_elfid = get_mapped_dso_elf_id(process, "/libpython")

        metadata = {
            "python_version": version,
            "exe_elfid": exe_elfid,
            "libpython_elfid": libpython_elfid,
            "sys_maxunicode": maxunicode,
        }

        metadata.update(super().make_application_metadata(process))
        return metadata


@register_profiler(
    "Python",
    profiler_name="PySpy",
    # py-spy is like pyspy, it's confusing and I mix between them
    possible_modes=["auto", "pyspy", "py-spy"],
    default_mode="auto",
    # we build pyspy for both,.
    supported_archs=["x86_64", "aarch64"],
    supported_windows_archs=["AMD64"],
    # profiler arguments are defined by preferred profiler of the runtime, that is PythonEbpfProfiler
    profiler_arguments=[],
    supported_profiling_modes=["cpu"],
)
class PySpyProfiler(SpawningProcessProfilerBase):
    MAX_FREQUENCY = 50
    _EXTRA_TIMEOUT = 10  # give py-spy some seconds to run (added to the duration)

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        *,
        python_mode: str,
        python_add_versions: bool,
    ):
        super().__init__(frequency, duration, profiler_state)
        if python_mode == "py-spy":
            python_mode = "pyspy"
        assert python_mode in ("auto", "pyspy"), f"unexpected mode: {python_mode}"
        self.add_versions = python_add_versions
        self._metadata = PythonMetadata(self._profiler_state.stop_event)

    def _make_command(self, pid: int, output_path: str, duration: int) -> List[str]:
        command = [
            resource_path("python/py-spy"),
            "record",
            "-r",
            str(self._frequency),
            "-d",
            str(duration),
            "--nonblocking",
            "--format",
            "raw",
            "-F",
            "--output",
            output_path,
            "-p",
            str(pid),
            "--full-filenames",
        ]
        if is_linux():
            command += ["--gil"]
        return command

    def _profile_process(self, process: Process, duration: int, spawned: bool) -> ProfileData:
        logger.info(
            f"Profiling{' spawned' if spawned else ''} process {process.pid} with py-spy",
            cmdline=process.cmdline(),
            no_extra_to_server=True,
        )
        container_name = self._profiler_state.get_container_name(process.pid)
        appid = application_identifiers.get_python_app_id(process)
        app_metadata = self._metadata.get_metadata(process)
        comm = process_comm(process)

        local_output_path = os.path.join(self._profiler_state.storage_dir, f"pyspy.{random_prefix()}.{process.pid}.col")
        with removed_path(local_output_path):
            try:
                run_process(
                    self._make_command(process.pid, local_output_path, duration),
                    stop_event=self._profiler_state.stop_event,
                    timeout=duration + self._EXTRA_TIMEOUT,
                    kill_signal=signal.SIGTERM if is_windows() else signal.SIGKILL,
                )
            except ProcessStoppedException:
                raise StopEventSetException
            except CalledProcessTimeoutError:
                logger.error(f"Profiling with py-spy timed out on process {process.pid}")
                raise
            except CalledProcessError as e:
                assert isinstance(e.stderr, str), f"unexpected type {type(e.stderr)}"

                if (
                    "Error: Failed to get process executable name. Check that the process is running.\n" in e.stderr
                    and not is_process_running(process)
                ):
                    logger.debug(f"Profiled process {process.pid} exited before py-spy could start")
                    return ProfileData(
                        self._profiling_error_stack("error", comm, "process exited before py-spy started"),
                        appid,
                        app_metadata,
                        container_name,
                    )
                raise

            logger.info(f"Finished profiling process {process.pid} with py-spy")
            parsed = parse_one_collapsed_file(Path(local_output_path), comm)
            if self.add_versions:
                parsed = _add_versions_to_process_stacks(process, parsed)
            return ProfileData(parsed, appid, app_metadata, container_name)

    def _select_processes_to_profile(self) -> List[Process]:
        filtered_procs = []
        if is_windows():
            all_processes = [x for x in pgrep_exe("python")]
        else:
            all_processes = [x for x in pgrep_maps(DETECTED_PYTHON_PROCESSES_REGEX)]

        for process in all_processes:
            try:
                if not self._should_skip_process(process):
                    filtered_procs.append(process)
            except NoSuchProcess:
                pass
            except Exception:
                logger.exception(f"Couldn't add pid {process.pid} to list")

        return filtered_procs

    def _should_profile_process(self, process: Process) -> bool:
        return search_proc_maps(process, DETECTED_PYTHON_PROCESSES_REGEX) is not None and not self._should_skip_process(
            process
        )

    def _should_skip_process(self, process: Process) -> bool:
        if process.pid == os.getpid():
            return True

        cmdline = " ".join(process.cmdline())
        if any(item in cmdline for item in _BLACKLISTED_PYTHON_PROCS):
            return True

        # PyPy is called pypy3 or pypy (for 2)
        # py-spy is, of course, only for CPython, and will report a possibly not-so-nice error
        # when invoked on pypy.
        # I'm checking for "pypy" in the basename here. I'm not aware of libpypy being directly loaded
        # into non-pypy processes, if we ever encounter that - we can check the maps instead
        if os.path.basename(process_exe(process)).startswith("pypy"):
            return True

        return False

    def check_readiness(self) -> bool:
        return True
