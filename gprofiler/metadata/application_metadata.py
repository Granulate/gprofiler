#
# Copyright (C) 2022 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import functools
from threading import Event, Lock
from typing import Any, Callable, Dict, Optional

from granulate_utils.linux.elf import elf_arch_to_uname_arch, get_elf_arch
from granulate_utils.linux.process import is_process_running, process_exe, read_process_execfn
from psutil import NoSuchProcess, Process, ZombieProcess

from gprofiler.log import get_logger_adapter
from gprofiler.metadata.versions import get_exe_version
from granulate_utils.gprofiler.platform import is_windows

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

        def _wrap_errors(fn: Callable[[Process], str], exception_log: str) -> str:
            try:
                return fn(process)
            except (NoSuchProcess, ZombieProcess):
                raise  # let caller handle
            except Exception as e:
                logger.exception(exception_log, pid=process.pid)
                return f"error: {e.__class__.__name__}"

        md["exe"] = _wrap_errors(process_exe, "Exception while reading process exe")
        md["execfn"] = _wrap_errors(
            lambda p: "error: not supported on Windows" if is_windows() else read_process_execfn(p),
            "Exception while reading process execfn",
        )
        # take arch from the executed elf, not the host system, because (although unlikely) it's possible
        # that the process runs a different, emulated architecture.
        md["arch"] = _wrap_errors(
            lambda p: (
                "error: not supported on Windows"
                if is_windows()
                else elf_arch_to_uname_arch(get_elf_arch(f"/proc/{process.pid}/exe"))
            ),
            "Exception while getting process exe architecture",
        )

        return md
