#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import sched
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from concurrent.futures._base import Future
from threading import Event, Lock, Thread
from typing import Dict, List, Optional

from granulate_utils.linux.proc_events import register_exec_callback, unregister_exec_callback
from granulate_utils.linux.process import is_process_running
from psutil import NoSuchProcess, Process

from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import ProcessToStackSampleCounters, StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.utils import limit_frequency

logger = get_logger_adapter(__name__)


class ProfilerInterface:
    """
    Interface class for all profilers
    """

    name: str

    def start(self) -> None:
        pass

    def snapshot(self) -> ProcessToStackSampleCounters:
        """
        :returns: Mapping from pid to stacks and their counts.
        """
        raise NotImplementedError

    def stop(self) -> None:
        pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()


class ProfilerBase(ProfilerInterface):
    """
    Base profiler class for all profilers.
    """

    MAX_FREQUENCY: Optional[int] = None
    MIN_DURATION: Optional[int] = None

    def __init__(self, frequency: int, duration: int, stop_event: Optional[Event], storage_dir: str):
        self._frequency = limit_frequency(self.MAX_FREQUENCY, frequency, self.__class__.__name__, logger)
        if self.MIN_DURATION is not None and duration < self.MIN_DURATION:
            raise ValueError(
                f"Minimum duration for {self.__class__.__name__} is {self.MIN_DURATION} (given {duration}), "
                "raise the duration in order to use this profiler"
            )
        self._duration = duration
        self._stop_event = stop_event or Event()
        self._storage_dir = storage_dir

        logger.info(
            f"Initialized {self.__class__.__name__} (frequency: {self._frequency}hz, duration: {self._duration}s)"
        )


class NoopProfiler(ProfilerInterface):
    """
    No-op profiler - used as a drop-in replacement for runtime profilers, when they are disabled.
    """

    def snapshot(self) -> ProcessToStackSampleCounters:
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

    def _profile_process(self, process: Process, duration: int) -> Optional[StackToSampleCount]:
        raise NotImplementedError

    def _notify_selected_processes(self, processes: List[Process]) -> None:
        pass

    def _wait_for_profiles(self, futures: Dict[Future, int]) -> ProcessToStackSampleCounters:
        results = {}
        for future in as_completed(futures):
            try:
                result = future.result()
                if result is not None:
                    results[futures[future]] = result
            except StopEventSetException:
                raise
            except NoSuchProcess:
                logger.debug(
                    f"{self.__class__.__name__}: process went down during profiling {futures[future]}",
                    exc_info=True,
                )
            except Exception:
                logger.exception(f"{self.__class__.__name__}: failed to profile process {futures[future]}")

        return results

    def snapshot(self) -> ProcessToStackSampleCounters:
        processes_to_profile = self._select_processes_to_profile()
        self._notify_selected_processes(processes_to_profile)

        if not processes_to_profile:
            return {}

        with ThreadPoolExecutor(max_workers=len(processes_to_profile)) as executor:
            futures: Dict[Future, int] = {}
            for process in processes_to_profile:
                futures[executor.submit(self._profile_process, process, self._duration)] = process.pid

            return self._wait_for_profiles(futures)


class SpawningProcessProfilerBase(ProcessProfilerBase):
    """
    Enhances ProcessProfilerBase with tracking of newly spawned processes.
    """

    _SCHED_THREAD_INTERVAL = 0.1
    _BACKOFF_INIT = 0.1
    # so we wait up to 1.5 seconds
    _BACKOFF_MAX = 0.8

    def __init__(self, frequency: int, duration: int, stop_event: Optional[Event], storage_dir: str):
        super().__init__(frequency, duration, stop_event, storage_dir)
        self._submit_lock = Lock()
        self._threads: Optional[ThreadPoolExecutor] = None
        self._start_ts: Optional[float] = None
        self._enabled_proc_events = False
        self._futures: Dict[Future, int] = {}
        self._sched = sched.scheduler()
        self._sched_stop = False
        self._sched_thread = Thread(target=self._sched_thread_run)

    def _should_profile_process(self, pid: int) -> bool:
        raise NotImplementedError

    def _notify_selected_processes(self, processes: List[Process]) -> None:
        # TODO ensure PIDs in _proc_exec_callback don't intersect with "processes"?
        # now we start watching for new processes.
        self._start_profiling_spawning()

    @property
    def _is_profiling_spawning(self) -> bool:
        return self._threads is not None

    def _start_profiling_spawning(self) -> None:
        with self._submit_lock:
            self._start_ts = time.monotonic()
            self._threads = ThreadPoolExecutor()

    def _stop_profiling_spawning(self) -> None:
        with self._submit_lock:
            self._start_ts = None
            self._threads = None

    def _proc_exec_callback(self, tid: int, pid: int) -> None:
        self._sched.enter(self._BACKOFF_INIT, 0, self._check_process, (Process(pid), self._BACKOFF_INIT))

    def start(self) -> None:
        super().start()

        self._sched_thread.start()

        try:
            register_exec_callback(self._proc_exec_callback)
        except Exception:
            logger.warning("Failed to enable proc_events listener for executed processes", exc_info=True)
        else:
            self._enabled_proc_events = True

    def stop(self) -> None:
        super().stop()

        if self._enabled_proc_events:
            unregister_exec_callback(self._proc_exec_callback)
            self._enabled_proc_events = False

        self._sched_stop = True
        self._sched_thread.join()

    def snapshot(self) -> ProcessToStackSampleCounters:
        results = super().snapshot()

        # wait for one duration, in case snapshot() found no processes
        self._stop_event.wait(self._duration)

        self._stop_profiling_spawning()
        results_spawned = self._wait_for_profiles(self._futures)
        self._futures = {}

        # should not intersect
        assert set(results).intersection(results_spawned) == set()
        results.update(results_spawned)
        return results

    def _sched_thread_run(self):
        while not (self._stop_event.is_set() or self._sched_stop):
            self._sched.run()
            self._stop_event.wait(0.1)

    def _check_process(self, process: Process, interval: float) -> None:
        # TODO try-except and ignore NoSuchProcess
        if is_process_running(process) and self._is_profiling_spawning:
            if self._should_profile_process(process.pid):
                # check again, with the lock this time
                with self._submit_lock:
                    if self._is_profiling_spawning:
                        # TODO ensure > 0 etc
                        assert self._start_ts is not None and self._threads is not None
                        duration = self._duration - (time.monotonic() - self._start_ts)
                        self._futures[self._threads.submit(self._profile_process, process, int(duration))] = process.pid
            else:
                if interval < self._BACKOFF_MAX:
                    new_interval = interval * 2
                    self._sched.enter(new_interval, 0, self._check_process, (process, new_interval))
