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

import concurrent.futures
import contextlib
import logging
import os
import sched
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures._base import Future
from threading import Lock, Thread
from types import TracebackType
from typing import Dict, List, Optional, Tuple, Type, TypeVar

import humanfriendly
from granulate_utils.linux.proc_events import register_exec_callback, unregister_exec_callback
from granulate_utils.linux.process import is_process_running
from psutil import NoSuchProcess, Process, ZombieProcess

from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import ProcessToProfileData, ProfileData, ProfilingErrorStack, StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import ProfileMetadata
from gprofiler.profiler_state import ProfilerState
from gprofiler.utils import limit_frequency
from gprofiler.utils.process import process_comm

logger = get_logger_adapter(__name__)


T = TypeVar("T", bound="ProfilerInterface")


class ProfilerInterface:
    """
    Interface class for all profilers
    """

    name: str

    def start(self) -> None:
        pass

    def snapshot(self) -> ProcessToProfileData:
        """
        :returns: Mapping from pid to `ProfileData`s.
        """
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def __enter__(self: T) -> T:
        self.start()
        return self

    def __exit__(
        self,
        exc_type: Optional[Type[BaseException]],
        exc_val: Optional[BaseException],
        exc_ctb: Optional[TracebackType],
    ) -> None:
        self.stop()


class ProfilerBase(ProfilerInterface):
    """
    Base profiler class for all profilers.
    """

    MAX_FREQUENCY: Optional[int] = None
    MIN_DURATION: Optional[int] = None

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        min_duration: int = 0,
    ):
        self._frequency = limit_frequency(
            self.MAX_FREQUENCY,
            frequency,
            self.__class__.__name__,
            logger,
            profiler_state.profiling_mode,
        )
        if self.MIN_DURATION is not None and duration < self.MIN_DURATION:
            raise ValueError(
                f"Minimum duration for {self.__class__.__name__} is {self.MIN_DURATION} (given {duration}), "
                "raise the duration in order to use this profiler"
            )
        self._duration = duration
        self._min_duration = min_duration
        self._profiler_state = profiler_state

        if profiler_state.profiling_mode == "allocation":
            frequency_str = f"allocation interval: {humanfriendly.format_size(frequency, binary=True)}"
        else:
            frequency_str = f"frequency: {frequency}hz"

        logger.info(
            f"Initialized {self.__class__.__name__} ({frequency_str}, duration: {self._duration}s), "
            f"profiling mode: {profiler_state.profiling_mode}"
        )


class SystemProfilerBase(ProfilerBase):
    def _is_system_wide_profiler(self) -> bool:
        return True

    def get_profile_metadata(self) -> ProfileMetadata:
        return {}


class NoopProfiler(ProfilerInterface):
    """
    No-op profiler - used as a drop-in replacement for runtime profilers, when they are disabled.
    """

    def snapshot(self) -> ProcessToProfileData:
        return {}

    @classmethod
    def is_noop_profiler(cls, profile_instance: ProfilerInterface) -> bool:
        return isinstance(profile_instance, cls)


class ProcessProfilerBase(ProfilerBase):
    """
    Base class for process-based profilers: those that operate on each process separately, thus need
    to be invoked for each PID.
    This class implements snapshot() for them - creates a thread that runs _profile_process() for each
    process that we wish to profile; then waits for all and returns the result.
    """

    def _select_processes_to_profile(self) -> List[Process]:
        raise NotImplementedError

    def _wait_for_profiles(self, futures: Dict[Future, Tuple[int, str]]) -> ProcessToProfileData:
        results = {}
        for future in concurrent.futures.as_completed(futures):
            pid, comm = futures[future]
            try:
                result = future.result()
                assert result is not None
            except StopEventSetException:
                raise
            except (NoSuchProcess, ZombieProcess):
                logger.debug(
                    f"{self.__class__.__name__}: process went down during profiling {pid} ({comm})",
                    exc_info=True,
                )
                result = ProfileData(
                    self._profiling_error_stack("error", "process went down during profiling", comm),
                    None,
                    None,
                    None,
                )
            except Exception as e:
                logger.exception(f"{self.__class__.__name__}: failed to profile process {pid} ({comm})")
                result = ProfileData(
                    self._profiling_error_stack("error", f"exception {type(e).__name__}", comm),
                    None,
                    None,
                    None,
                )

            results[pid] = result

        return results

    def _profile_process(self, process: Process, duration: int, spawned: bool) -> ProfileData:
        raise NotImplementedError

    def _notify_selected_processes(self, processes: List[Process]) -> None:
        pass

    def _should_limit_processes(self) -> bool:
        """
        Override this in profilers that should NOT respect the max_processes_per_profiler limit.
        System-wide profilers (perf, eBPF) should return False.
        Runtime profilers (py-spy, Java, Ruby, etc.) should return True (default).
        """
        return True

    def _is_system_wide_profiler(self) -> bool:
        """
        Override this in system-wide profilers (perf, eBPF) to return True.
        These profilers can be disabled when system has too many processes.
        """
        return False

    def _get_top_processes_by_cpu(self, processes: List[Process], max_processes: int) -> List[Process]:
        """
        Filter processes to the top N by CPU usage to reduce memory consumption.

        Args:
            processes: List of processes to filter
            max_processes: Maximum number of processes to return

        Returns:
            List of top N processes by CPU usage, or all processes if max_processes <= 0
        """
        if max_processes <= 0 or len(processes) <= max_processes:
            return processes

        logger.info(
            f"{self.__class__.__name__}: Limiting to top {max_processes} processes "
            f"(from {len(processes)}) by CPU usage to reduce memory consumption"
        )

        # Get CPU usage for each process, handling exceptions gracefully
        processes_with_cpu = []
        for process in processes:
            try:
                # Use short interval for CPU measurement to avoid blocking
                cpu_percent = process.cpu_percent(interval=0.1)
                processes_with_cpu.append((process, cpu_percent))
            except (NoSuchProcess, ZombieProcess, PermissionError):
                # Process may have died or we don't have permission
                # Still include it with 0% CPU so it's considered but deprioritized
                processes_with_cpu.append((process, 0.0))
            except Exception as e:
                logger.debug(f"Error getting CPU usage for process {process.pid}: {e}")
                processes_with_cpu.append((process, 0.0))

        # Sort by CPU usage (descending) and take top N
        processes_with_cpu.sort(key=lambda x: x[1], reverse=True)
        top_processes = [proc for proc, cpu in processes_with_cpu[:max_processes]]

        if logger.isEnabledFor(logging.DEBUG):
            top_cpu_info = [(proc.pid, cpu) for proc, cpu in processes_with_cpu[: min(5, max_processes)]]
            logger.debug(f"{self.__class__.__name__}: Selected top processes by CPU: {top_cpu_info}")

        return top_processes

    def _get_process_age(self, process: Process) -> float:
        """Get the age of a process in seconds."""
        try:
            return float(time.time() - process.create_time())
        except (NoSuchProcess, ZombieProcess):
            return 0.0

    @staticmethod
    def _profiling_error_stack(
        what: str,
        reason: str,
        comm: str,
    ) -> StackToSampleCount:
        # return 1 sample, it will be scaled later in merge_profiles().
        # if --perf-mode=none mode is used, it will not, but we don't have anything logical to
        # do here in that case :/
        return ProfilingErrorStack(what, reason, comm)

    def snapshot(self) -> ProcessToProfileData:
        processes_to_profile = self._select_processes_to_profile()
        logger.debug(f"{self.__class__.__name__}: selected {len(processes_to_profile)} processes to profile")
        if self._profiler_state.processes_to_profile is not None and len(processes_to_profile) > 0:
            processes_to_profile = [
                process for process in processes_to_profile if process in self._profiler_state.processes_to_profile
            ]
            logger.debug(f"{self.__class__.__name__}: processes left after filtering: {len(processes_to_profile)}")

        # Apply max_processes_per_profiler limit for runtime profilers (not system-wide profilers)
        if self._should_limit_processes() and self._profiler_state.max_processes_per_profiler > 0:
            processes_to_profile = self._get_top_processes_by_cpu(
                processes_to_profile, self._profiler_state.max_processes_per_profiler
            )

        self._notify_selected_processes(processes_to_profile)

        if not processes_to_profile:
            return {}

        with ThreadPoolExecutor(max_workers=len(processes_to_profile)) as executor:
            futures: Dict[Future, Tuple[int, str]] = {}
            for process in processes_to_profile:
                try:
                    comm = process_comm(process)
                except (NoSuchProcess, ZombieProcess):
                    continue

                futures[executor.submit(self._profile_process, process, self._duration, False)] = (process.pid, comm)

            return self._wait_for_profiles(futures)


class SpawningProcessProfilerBase(ProcessProfilerBase):
    """
    Enhances ProcessProfilerBase with tracking of newly spawned processes.
    """

    _SCHED_THREAD_INTERVAL = 0.1
    _BACKOFF_INIT = 0.1
    # so we wait up to 1.5 seconds
    _BACKOFF_MAX = 0.8

    def __init__(
        self,
        frequency: int,
        duration: int,
        profiler_state: ProfilerState,
        min_duration: int = 0,
    ):
        super().__init__(frequency, duration, profiler_state, min_duration)
        self._submit_lock = Lock()
        self._threads: Optional[ThreadPoolExecutor] = None
        self._start_ts: Optional[float] = None
        self._preexisting_pids: Optional[List[int]] = None
        self._enabled_proc_events_spawning = False
        self._futures: Dict[Future, Tuple[int, str]] = {}
        self._sched = sched.scheduler()
        self._sched_stop = False
        self._sched_thread = Thread(target=self._sched_thread_run)

    def _should_profile_process(self, process: Process) -> bool:
        raise NotImplementedError

    def _notify_selected_processes(self, processes: List[Process]) -> None:
        # now we start watching for new processes.
        self._start_profiling_spawning(processes)

    @property
    def _is_profiling_spawning(self) -> bool:
        return self._threads is not None

    def _start_profiling_spawning(self, processes: List[Process]) -> None:
        with self._submit_lock:
            self._start_ts = time.monotonic()
            # arbitrary high number of threads to make sure we can run profiling of many
            # processes concurrently
            self._threads = ThreadPoolExecutor(max_workers=999)
            # TODO: add proc_events exit action to remove these
            self._preexisting_pids = [p.pid for p in processes]

    def _stop_profiling_spawning(self) -> None:
        with self._submit_lock:
            self._start_ts = None
            assert self._threads is not None
            threads = self._threads
            # delete it before blocking on the exit of all threads (to ensure no new work
            # is added)
            self._threads = None
            threads.shutdown()  # waits (although - all are done by now)
            self._preexisting_pids = None

    def _proc_exec_callback(self, tid: int, pid: int) -> None:
        if not self._is_profiling_spawning:
            return

        with contextlib.suppress(NoSuchProcess):
            self._sched.enter(
                self._BACKOFF_INIT,
                0,
                self._check_process,
                (Process(pid), self._BACKOFF_INIT),
            )

    def start(self) -> None:
        super().start()

        if self._profiler_state.profile_spawned_processes:
            logger.debug(f"{self.__class__.__name__}: starting profiling spawning processes")
            self._sched_thread.start()

            try:
                register_exec_callback(self._proc_exec_callback)
            except Exception:
                logger.warning(
                    "Failed to enable proc_events listener for executed processes",
                    exc_info=True,
                )
            else:
                self._enabled_proc_events_spawning = True

    def stop(self) -> None:
        super().stop()

        if self._profiler_state.profile_spawned_processes:
            if self._enabled_proc_events_spawning:
                unregister_exec_callback(self._proc_exec_callback)
                self._enabled_proc_events_spawning = False

            self._sched_stop = True
            self._sched_thread.join()

    def snapshot(self) -> ProcessToProfileData:
        end_ts = time.monotonic() + self._duration

        results = super().snapshot()

        # wait for the duration, in case snapshot() found no processes and returns immediately.
        duration_left = end_ts - time.monotonic()
        if duration_left > 0:
            self._profiler_state.stop_event.wait(duration_left)

        self._stop_profiling_spawning()
        self._clear_sched()
        results_spawned = self._wait_for_profiles(self._futures)
        self._futures = {}

        # should not intersect
        assert set(results).intersection(results_spawned) == set()
        results.update(results_spawned)
        return results

    def _sched_thread_run(self) -> None:
        while not (self._profiler_state.stop_event.is_set() or self._sched_stop):
            self._sched.run()
            self._profiler_state.stop_event.wait(0.1)

    def _clear_sched(self) -> None:
        for event in self._sched.queue:
            self._sched.cancel(event)

    def _check_process(self, process: Process, interval: float) -> None:
        with contextlib.suppress(NoSuchProcess):
            if not self._is_profiling_spawning or not is_process_running(process) or process.ppid() == os.getpid():
                return

            if self._should_profile_process(process):
                # check again, with the lock this time
                with self._submit_lock:
                    if self._is_profiling_spawning:
                        assert (
                            self._start_ts is not None
                            and self._threads is not None
                            and self._preexisting_pids is not None
                        )
                        if process.pid in self._preexisting_pids:
                            return

                        duration = self._duration - (time.monotonic() - self._start_ts)
                        if duration <= 0:
                            return

                        comm = process_comm(process)
                        self._futures[self._threads.submit(self._profile_process, process, int(duration), True)] = (
                            process.pid,
                            comm,
                        )
            else:
                if interval < self._BACKOFF_MAX:
                    new_interval = interval * 2
                    self._sched.enter(new_interval, 0, self._check_process, (process, new_interval))
