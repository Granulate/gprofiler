import statistics
import time
from abc import ABCMeta, abstractmethod
from collections import deque
from threading import Event, Thread
from typing import Deque, List, Optional, Tuple

import psutil

from gprofiler.exceptions import ThreadStopTimeoutError

DEFAULT_POLLING_INTERVAL_SECONDS = 5
STOP_TIMEOUT_SECONDS = 30


class SystemMetricsMonitorBase(metaclass=ABCMeta):
    @abstractmethod
    def start(self):
        pass

    @abstractmethod
    def stop(self):
        pass

    @abstractmethod
    def get_average_memory_utilization(self) -> Optional[float]:
        raise NotImplementedError

    @abstractmethod
    def get_cpu_utilization(self) -> Optional[float]:
        """
        Returns the CPU utilization percentage since the last time this method was called.
        """
        raise NotImplementedError


class SystemMetricsMonitor(SystemMetricsMonitorBase):
    def __init__(self, max_memory_poll_age_seconds: int, polling_rate_seconds: int = DEFAULT_POLLING_INTERVAL_SECONDS):
        self._polling_rate_seconds = polling_rate_seconds
        self._cpu_count = psutil.cpu_count() or 1
        self._mem_percentages: Deque[float] = deque(maxlen=round(max_memory_poll_age_seconds / polling_rate_seconds))
        self._last_cpu_poll_time: Optional[float] = None
        self._last_cpu_utilization_percentages: Optional[Tuple[float, float]] = None
        self._stop_event = Event()
        self._thread = None

        self.get_cpu_utilization()  # Call this once to set the necessary data

    def start(self):
        assert self._thread is None, "SystemMetricsMonitor is already running"
        self._stop_event.clear()
        self._thread = Thread(target=self._continuously_polling_memory, args=(self._polling_rate_seconds,))
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        assert self._thread is not None, "SystemMetricsMonitor was not running"
        self._thread.join(STOP_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            raise ThreadStopTimeoutError("Timed out while waiting for the SystemMetricsMonitor internal thread to stop")
        self._thread = None

    def _continuously_polling_memory(self, polling_rate_seconds: int):
        while not self._stop_event.is_set():
            start_time = time.monotonic()
            current_ram_percent = psutil.virtual_memory().percent
            self._mem_percentages.append(current_ram_percent)
            elapsed = time.monotonic() - start_time
            self._stop_event.wait(timeout=polling_rate_seconds - elapsed)

    def get_average_memory_utilization(self) -> Optional[float]:
        current_length = len(self._mem_percentages)
        if current_length == 0:
            return None
        all_percentages: List[float] = []
        # Avoid a race condition by only fetching the current X values. More values can be added in the meantime by
        # other threads, which will be popped in the next call.
        for _ in range(current_length):
            all_percentages.append(self._mem_percentages.popleft())
        return statistics.mean(all_percentages)

    def get_cpu_utilization(self) -> Optional[float]:
        """
        Returns the CPU utilization percentage since the last time this method was called.
        Based on the psutil.cpu_percent method.
        """
        last_user, last_system = self._last_cpu_utilization_percentages or (None, None)
        current_cpu_times = psutil.cpu_times()
        current_user, current_system = current_cpu_times.user, current_cpu_times.system
        self._last_cpu_utilization_percentages = (current_user, current_system)
        last_time = self._last_cpu_poll_time
        current_time = self._last_cpu_poll_time = time.monotonic() * self._cpu_count

        if None in (last_user, last_system):
            return 0.0
        assert last_time is not None
        delta_cpu = (current_user - last_user) + (current_system - last_system)
        delta_time = current_time - last_time

        try:
            overall_cpu_percent = (delta_cpu / delta_time) * 100
        except ZeroDivisionError:
            # There was no interval between calls
            return 0.0
        else:
            return round(overall_cpu_percent, 1)


class NoopSystemMetricsMonitor(SystemMetricsMonitorBase):
    def start(self):
        pass

    def stop(self):
        pass

    def get_average_memory_utilization(self) -> Optional[float]:
        return None

    def get_cpu_utilization(self) -> Optional[float]:
        return None
