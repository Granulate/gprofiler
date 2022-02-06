import statistics
from abc import ABCMeta, abstractmethod
from dataclasses import dataclass
from threading import Event, RLock, Thread
from typing import List, Optional

import psutil

from gprofiler.exceptions import ThreadStopTimeoutError

DEFAULT_POLLING_INTERVAL_SECONDS = 5
STOP_TIMEOUT_SECONDS = 2


@dataclass
class Metrics:
    # The average CPU usage between gProfiler cycles
    cpu_avg: Optional[float]
    # The average RAM usage between gProfiler cycles
    mem_avg: Optional[float]


class SystemMetricsMonitorBase(metaclass=ABCMeta):
    @abstractmethod
    def start(self) -> None:
        pass

    @abstractmethod
    def stop(self) -> None:
        pass

    @abstractmethod
    def _get_average_memory_utilization(self) -> Optional[float]:
        raise NotImplementedError

    @abstractmethod
    def _get_cpu_utilization(self) -> Optional[float]:
        """
        Returns the CPU utilization percentage since the last time this method was called.
        """
        raise NotImplementedError

    def get_metrics(self) -> Metrics:
        return Metrics(self._get_cpu_utilization(), self._get_average_memory_utilization())


class SystemMetricsMonitor(SystemMetricsMonitorBase):
    def __init__(self, stop_event: Event, polling_rate_seconds: int = DEFAULT_POLLING_INTERVAL_SECONDS):
        self._polling_rate_seconds = polling_rate_seconds
        self._mem_percentages: List[float] = []
        self._stop_event = stop_event
        self._thread: Optional[Thread] = None
        self._lock = RLock()

        self._get_cpu_utilization()  # Call this once to set the necessary data

    def start(self) -> None:
        assert self._thread is None, "SystemMetricsMonitor is already running"
        assert not self._stop_event.is_set(), "Stop condition is already set (perhaps gProfiler was already stopped?)"
        self._thread = Thread(target=self._continuously_poll_memory, args=(self._polling_rate_seconds,))
        self._thread.start()

    def stop(self) -> None:
        assert self._thread is not None, "SystemMetricsMonitor is not running"
        assert self._stop_event.is_set(), "Stop event was not set before stopping the SystemMetricsMonitor"
        self._thread.join(STOP_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            raise ThreadStopTimeoutError("Timed out while waiting for the SystemMetricsMonitor internal thread to stop")
        self._thread = None

    def _continuously_poll_memory(self, polling_rate_seconds: int) -> None:
        while not self._stop_event.is_set():
            current_ram_percent = psutil.virtual_memory().percent  # type: ignore # virtual_memory doesn't have a
            # return type is types-psutil
            self._mem_percentages.append(current_ram_percent)
            self._stop_event.wait(timeout=polling_rate_seconds)

    def _get_average_memory_utilization(self) -> Optional[float]:
        # Make sure there's only one thread that takes out the values
        # NOTE - Since there's currently only a single consumer, this is not necessary but is done to support multiple
        # consumers.
        with self._lock:
            current_length = len(self._mem_percentages)
            if current_length == 0:
                return None
            average_memory = statistics.mean(self._mem_percentages[:current_length])
            self._mem_percentages[:current_length] = []
            return average_memory

    def _get_cpu_utilization(self) -> float:
        # None-blocking call. Must be called at least once before attempting to get a meaningful value.
        # See `psutil.cpu_percent` documentation.
        return psutil.cpu_percent(interval=None)


class NoopSystemMetricsMonitor(SystemMetricsMonitorBase):
    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def _get_average_memory_utilization(self) -> Optional[float]:
        return None

    def _get_cpu_utilization(self) -> Optional[float]:
        return None
