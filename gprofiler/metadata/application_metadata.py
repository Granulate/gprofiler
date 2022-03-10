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

    @classmethod
    def get_metadata(cls, process: Process) -> Optional[Dict]:
        return cls._cache.get(process)

    @classmethod
    def _clear_cache(cls) -> None:
        with cls._cache_clear_lock:
            for process in list(cls._cache.keys()):
                if not is_process_running(process):
                    del cls._cache[process]

    @classmethod
    def update_metadata(cls, process: Process, stop_event: Event) -> None:
        if process not in cls._cache:
            if len(cls._cache) > cls._CACHE_CLEAR_ON_SIZE:
                cls._clear_cache()
            cls._cache[process] = cls.make_application_metadata(process, stop_event)

    @classmethod
    def make_application_metadata(cls, process: Process, stop_event: Event) -> Dict[str, Any]:
        return {"exe": process.exe(), "execfn": read_process_execfn(process)}
