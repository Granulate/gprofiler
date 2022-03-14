#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from threading import Event, Lock
from typing import Any, Dict, Optional, Union

from granulate_utils.linux.process import is_process_running
from psutil import NoSuchProcess, Process

from gprofiler.log import get_logger_adapter
from gprofiler.utils.elf import read_process_execfn

logger = get_logger_adapter(__name__)


def get_application_metadata(process: Union[int, Process]) -> Optional[Dict]:
    if process in (0, -1):  # funny values retrieved by perf
        return None

    try:
        process = process if isinstance(process, Process) else Process(process)
    except NoSuchProcess:
        return None

    return ApplicationMetadata.get_metadata(process)


class ApplicationMetadata:
    # chosen arbitrarily to be large enough to contain all processes we could possibly profile in one session; while
    # not exploding memory too much.
    _CACHE_CLEAR_ON_SIZE = 0x4000
    _cache: Dict[Process, Optional[Dict]] = {}
    _cache_clear_lock = Lock()
    _metadata_exception_logs = 0
    _MAX_METADATA_EXCEPTION_LOGS = 100

    def __init__(self, stop_event: Event):
        self._stop_event = stop_event

    @classmethod
    def get_metadata(cls, process: Process) -> Optional[Dict]:
        return cls._cache.get(process)

    def _clear_cache(self) -> None:
        with self._cache_clear_lock:
            for process in list(self._cache.keys()):
                if not is_process_running(process):
                    del self._cache[process]

    def update_metadata(self, process: Process) -> None:
        if process not in self._cache:
            if len(self._cache) > self._CACHE_CLEAR_ON_SIZE:
                self._clear_cache()
            try:
                metadata = self.make_application_metadata(process)
            except NoSuchProcess:
                # let our caller handler this
                raise
            except Exception:
                if self._metadata_exception_logs > self._MAX_METADATA_EXCEPTION_LOGS:
                    logger.exception(f"Exception while collecting metadata in {self.__class__.__name__}!")
            else:
                self._cache[process] = metadata

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        return {"exe": process.exe(), "execfn": read_process_execfn(process)}
