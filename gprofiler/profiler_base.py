#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from threading import Event
from typing import Optional

from gprofiler.log import get_logger_adapter
from gprofiler.types import ProcessToStackSampleCounters
from gprofiler.utils import limit_frequency

logger = get_logger_adapter(__name__)


class ProfilerInterface:
    """
    Interface class for all profilers
    """

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

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Optional[Event],
        storage_dir: str,
    ):
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
