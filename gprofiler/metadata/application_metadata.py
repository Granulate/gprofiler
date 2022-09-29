#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import functools
from subprocess import CompletedProcess
from threading import Event, Lock
from typing import Any, Dict, Optional

from granulate_utils.linux.ns import get_process_nspid, run_in_ns
from granulate_utils.linux.process import is_process_running, read_process_execfn
from psutil import NoSuchProcess, Process

from gprofiler.log import get_logger_adapter
from gprofiler.platform import is_windows
from gprofiler.utils import run_process

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
        """
        Runs {process.exe()} --version in the appropriate namespace
        """
        exe_path = f"/proc/{get_process_nspid(process.pid)}/exe"

        def _run_get_version() -> "CompletedProcess[bytes]":
            return run_process([exe_path, version_arg], stop_event=self._stop_event, timeout=self._GET_VERSION_TIMEOUT)

        cp = run_in_ns(["pid", "mnt"], _run_get_version, process.pid)
        stdout = cp.stdout.decode().strip()
        # return stderr if stdout is empty, some apps print their version to stderr.
        if try_stderr and not stdout:
            return cp.stderr.decode().strip()

        return stdout

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
            except NoSuchProcess:
                # let our caller handler this
                raise
            except Exception:
                # log only the first _MAX_METADATA_EXCEPTION_LOGS exceptions; I expect the same exceptions to
                # be repeated again and again, so it's enough to log just a handful of them.
                if self._metadata_exception_logs_count < self._MAX_METADATA_EXCEPTION_LOGS:
                    logger.exception(f"Exception while collecting metadata in {self.__class__.__name__}!")
                    self._metadata_exception_logs_count += 1
            else:
                self._cache[process] = metadata

        return metadata

    def make_application_metadata(self, process: Process) -> Dict[str, Any]:
        return {"exe": process.exe(), "execfn": None if is_windows() else read_process_execfn(process)}
