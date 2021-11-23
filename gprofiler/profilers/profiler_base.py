#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import concurrent.futures
import json
from contextlib import contextmanager
from threading import Event
from typing import List, Optional

from psutil import NoSuchProcess, Process
from utils.linux.oom import get_oom_entry
from utils.linux.signals import get_signal_entry

from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import ProcessToStackSampleCounters, StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.utils import is_process_running, limit_frequency

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

    profiled_processes = set()

    @classmethod
    def prune_profiled_processes(cls):
        for proc in set(cls.profiled_processes):
            if not is_process_running(proc):
                cls.profiled_processes.remove(proc)

    def _select_processes_to_profile(self) -> List[Process]:
        raise NotImplementedError

    def _profile_process(self, process: Process) -> Optional[StackToSampleCount]:
        raise NotImplementedError

    def _profile_process_wrapper(self, process: Process):
        self.profiled_processes.add(process)
        return self._profile_process(process)

    def snapshot(self) -> ProcessToStackSampleCounters:
        processes_to_profile = self._select_processes_to_profile()
        if not processes_to_profile:
            return {}

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(processes_to_profile)) as executor:
            futures = {}
            for process in processes_to_profile:
                futures[executor.submit(self._profile_process_wrapper, process)] = process.pid

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


def _handle_kernel_messages(messages):
    profiled_pids = {proc.pid for proc in ProcessProfilerBase.profiled_processes}

    for message in messages:
        _, _, text = message
        entry = get_oom_entry(text)
        if entry and entry.pid in profiled_pids:
            logger.info(f"OOM: {json.dumps(entry._asdict())}")

        entry = get_signal_entry(text)
        if entry and entry.pid in profiled_pids:
            logger.info(f"Signaled: {json.dumps(entry._asdict())}")


def handle_new_kernel_messages(kernel_messages_provider):
    try:
        messages = list(kernel_messages_provider.iter_new_messages())
    except Exception:
        logger.exception("Error iterating new kernel messages")
    else:
        _handle_kernel_messages(messages)
    finally:
        ProcessProfilerBase.prune_profiled_processes()
