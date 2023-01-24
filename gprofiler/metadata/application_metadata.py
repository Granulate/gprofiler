#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import functools
from threading import Event, Lock
from typing import Any, Dict, Optional

from granulate_utils.linux.process import is_process_running, process_exe, read_process_execfn
from psutil import NoSuchProcess, Process, ZombieProcess

from gprofiler.log import get_logger_adapter
from gprofiler.metadata.versions import get_exe_version
from gprofiler.platform import is_windows

logger = get_logger_adapter(__name__)


class ApplicationMetadata:
    # chosen arbitrarily to be large enough to contain all processes we could possibly profile in one session; while
    # not exploding memory too much.
    _CACHE_CLEAR_ON_SIZE = 0x4000
    _cache: Dict[Process, Optional[Dict]] = {}
    _cache_clear_lock = Lock()
    _metadata_exception_logs_count = 0
    _MAX_METADATA_EXCEPTION_LOGS = 100
    _GET_VERSION_TIMEOUT = 3

    def __init__(self, stop_event: Event):
        self._stop_event = stop_event

    def _clear_cache(self) -> None:
        with self._cache_clear_lock:
            for process in list(self._cache.keys()):
                if not is_process_running(process):
                    del self._cache[process]

    def get_exe_version(self, process: Process, version_arg: str = "--version", try_stderr: bool = False) -> str:
        return get_exe_version(process, self._stop_event, self._GET_VERSION_TIMEOUT, version_arg, try_stderr)

    @functools.lru_cache(4096)
    def get_exe_version_cached(self, process: Process, version_arg: str = "--version", try_stderr: bool = False) -> str:
        return self.get_exe_version(process, version_arg, try_stderr)

    def get_metadata(self, process: Process) -> Optional[Dict]:
        metadata = self._cache.get(process)
        if metadata is None:
            if len(self._cache) > self._CACHE_CLEAR_ON_SIZE:
                self._clear_cache()
            try:
                metadata = self.make_application_metadata(process)
            except (NoSuchProcess, ZombieProcess):
                # let our caller handler this
                raise
            except Exception:
                # log only the first _MAX_METADATA_EXCEPTION_LOGS exceptions; I expect the same exceptions to
                # be repeated again and again, so it's enough to log just a handful of them.
                if self._metadata_exception_logs_count < self._MAX_METADATA_EXCEPTION_LOGS:
                    logger.exception(
                        f"Exception while collecting metadata in {self.__class__.__name__}!", pid=process.pid
                    )
                    self._metadata_exception_logs_count += 1
            else:
                self._cache[process] = metadata

        return metadata

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        md = {}

        try:
            exe = process_exe(process)
        except (NoSuchProcess, ZombieProcess):
            raise  # let caller handle
        except Exception as e:
            logger.exception("Exception while reading process exe", pid=process.pid)
            exe = f"error: {e.__class__.__name__}"
        md["exe"] = exe

        try:
            execfn = "error: not supported on Windows" if is_windows() else read_process_execfn(process)
        except (NoSuchProcess, ZombieProcess):
            raise  # let caller handle
        except Exception as e:
            logger.exception("Exception while reading process execfn", pid=process.pid)
            execfn = f"error: {e.__class__.__name__}"
        md["execfn"] = execfn

        return md
