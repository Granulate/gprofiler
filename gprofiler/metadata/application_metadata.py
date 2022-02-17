#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from threading import Event, Lock
from typing import Dict, Optional, Union

from granulate_utils.linux.process import is_process_running
from psutil import Process

from gprofiler.utils.elf import get_elf_buildid


def get_application_metadata(process: Union[int, Process]) -> Optional[Dict]:
    pid = process if isinstance(process, int) else process.pid
    try:
        buildid = get_elf_buildid(f"/proc/{pid}/exe") if pid != 0 else None
    except FileNotFoundError:
        buildid = None
    return {"build_id": buildid}


class ApplicationMetadata:
    _CACHE_CLEAR_ON_SIZE = 16384
    _cache: Dict[Process, Optional[Dict]] = {}
    _cache_clear_lock = Lock()

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
    def make_application_metadata(cls, process: Process, stop_event: Event) -> Optional[Dict]:
        return None
