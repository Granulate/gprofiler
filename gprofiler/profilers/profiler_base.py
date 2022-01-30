#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import concurrent.futures
from threading import Event
from types import TracebackType
from typing import List, Optional, Type, TypeVar

from psutil import NoSuchProcess, Process

from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import ProcessToStackSampleCounters, StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.utils import limit_frequency

logger = get_logger_adapter(__name__)


T = TypeVar('T', bound='ProfilerInterface')


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

    def __enter__(self: T) -> T:
        self.start()
        return self

    def __exit__(self, exc_type: Optional[Type[BaseException]], exc_val: Optional[BaseException],
                 exc_ctb: Optional[TracebackType]) -> None:
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

    def _profile_process(self, process: Process) -> Optional[StackToSampleCount]:
        raise NotImplementedError

    def snapshot(self) -> ProcessToStackSampleCounters:
        processes_to_profile = self._select_processes_to_profile()
        if not processes_to_profile:
            return {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(processes_to_profile)) as executor:
            futures = {}
            for process in processes_to_profile:
                futures[executor.submit(self._profile_process, process)] = process.pid

            results = {}
            for future in concurrent.futures.as_completed(futures):
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
