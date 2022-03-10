#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from threading import Event, Lock
from typing import Any, Dict, Optional, Union

from granulate_utils.linux.process import is_process_running
from psutil import NoSuchProcess, Process

from gprofiler.utils.elf import read_process_execfn


def get_application_metadata(process: Union[int, Process]) -> Optional[Dict]:
    if process in (0, -1):  # funny values retrieved by perf
        return None

    try:
        process = process if isinstance(process, Process) else Process(process)
    except NoSuchProcess:
        return None

    return ApplicationMetadata.get_metadata(process)


class ApplicationMetadata:
    _CACHE_CLEAR_ON_SIZE = 16384
    _cache: Dict[Process, Optional[Dict]] = {}
    _cache_clear_lock = Lock()

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
            self._cache[process] = self.make_application_metadata(process)

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        return {"exe": process.exe(), "execfn": read_process_execfn(process)}
