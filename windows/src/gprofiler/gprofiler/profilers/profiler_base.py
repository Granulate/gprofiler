#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import concurrent.futures
from collections import Counter
from threading import Event
from types import TracebackType
from typing import List, Optional, Type, TypeVar

from psutil import NoSuchProcess, Process

from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import ProcessToProfileData, ProfileData, StackToSampleCount
from gprofiler.log import get_logger_adapter
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

    def _profile_process(self, process: Process) -> ProfileData:
        raise NotImplementedError

    @staticmethod
    def _profiling_error_stack(
        what: str,
        reason: str,
        comm: str,
    ) -> StackToSampleCount:
        # return 1 sample, it will be scaled later in merge_profiles().
        # if --perf-mode=none mode is used, it will not, but we don't have anything logical to
        # do here in that case :/
        return Counter({f"{comm};[Profiling {what}: {reason}]": 1})

    def snapshot(self) -> ProcessToProfileData:
        processes_to_profile = self._select_processes_to_profile()
        if not processes_to_profile:
            return {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(processes_to_profile)) as executor:
            futures = {}
            for process in processes_to_profile:
                try:
                    comm = process_comm(process)
                except NoSuchProcess:
                    logger.debug("No such process: {process.pid}")
                    continue

                futures[executor.submit(self._profile_process, process)] = (process.pid, comm)

            results = {}
            for future in concurrent.futures.as_completed(futures):
                pid, comm = futures[future]
                try:
                    result = future.result()
                    assert result is not None
                except StopEventSetException:
                    raise
                except NoSuchProcess:
                    logger.debug(
                        f"{self.__class__.__name__}: process went down during profiling {pid} ({comm})",
                        exc_info=True,
                    )
                    result = ProfileData(
                        self._profiling_error_stack("error", "process went down during profiling", comm), None, None
                    )
                except Exception as e:
                    logger.exception(f"{self.__class__.__name__}: failed to profile process {pid} ({comm})")
                    result = ProfileData(
                        self._profiling_error_stack("error", f"exception {type(e).__name__}", comm), None, None
                    )

                results[pid] = result
        logger.debug(f"Result snapshot: {results}")

        return results
