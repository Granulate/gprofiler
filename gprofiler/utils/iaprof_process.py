#
# Copyright (C) 2026 Intel Corporation
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

import concurrent.futures
import signal
from collections import deque
from subprocess import Popen, TimeoutExpired
from threading import Lock, Thread
from typing import Deque, List, Optional, TextIO

from gprofiler.gprofiler_types import ProcessToStackSampleCounters
from gprofiler.log import get_logger_adapter
from gprofiler.utils import cleanup_process_reference, start_process
from gprofiler.utils.iaprof import IaprofOutputReader

logger = get_logger_adapter(__name__)


class IaprofProcessError(RuntimeError):
    pass


class IaprofProcess:
    _STDERR_LINES = 200

    def __init__(
        self,
        path: str,
        interval_ms: int,
        eu_stall_subsample: int,
        startup_timeout: float = 10,
        stop_timeout: float = 5,
    ) -> None:
        if interval_ms <= 0 or eu_stall_subsample <= 0:
            raise ValueError("iaprof interval and EU stall subsample must be positive")

        self._path = path
        self._interval_ms = interval_ms
        self._eu_stall_subsample = eu_stall_subsample
        self._startup_timeout = startup_timeout
        self._stop_timeout = stop_timeout
        self._process: Optional[Popen[str]] = None
        self._reader: Optional[IaprofOutputReader] = None
        self._stdout_thread: Optional[Thread] = None
        self._stderr_thread: Optional[Thread] = None
        self._stderr_lines: Deque[str] = deque(maxlen=self._STDERR_LINES)
        self._stderr_lock = Lock()

    def start(self) -> None:
        if self._process is not None:
            raise RuntimeError("iaprof is already started")

        self._reader = IaprofOutputReader()
        self._stderr_lines.clear()
        process = start_process(
            self._command(),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        assert process.stdout is not None and process.stderr is not None
        self._process = process

        try:
            self._stdout_thread = Thread(
                target=self._reader.read,
                args=(process.stdout,),
                name="iaprof-stdout",
            )
            self._stderr_thread = Thread(
                target=self._read_stderr,
                args=(process.stderr,),
                name="iaprof-stderr",
            )
            self._stdout_thread.start()
            self._stderr_thread.start()
            self._reader.wait_until_ready(self._startup_timeout)
        except Exception as error:
            self._stop_after_failure()
            raise IaprofProcessError(self._error_message("iaprof failed to start")) from error
        except BaseException:
            self._stop_after_failure()
            raise

    def snapshot(self, timeout: float) -> ProcessToStackSampleCounters:
        process = self._process
        if process is None or self._reader is None:
            raise RuntimeError("iaprof is not started")
        try:
            return self._reader.request_snapshot().result(timeout)
        except concurrent.futures.TimeoutError as error:
            process.kill()
            try:
                process.wait(timeout=self._stop_timeout)
            except TimeoutExpired as wait_error:
                raise IaprofProcessError("iaprof failed to stop after SIGKILL") from wait_error
            raise IaprofProcessError(self._error_message("iaprof snapshot timed out")) from error
        except Exception as error:
            raise IaprofProcessError(self._error_message("iaprof snapshot failed")) from error

    def stop(self) -> None:
        process = self._process
        if process is None:
            return

        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=self._stop_timeout)
            except TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=self._stop_timeout)
                except TimeoutExpired as error:
                    raise IaprofProcessError("iaprof failed to stop after SIGKILL") from error

        try:
            self._join_threads()
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
            cleanup_process_reference(process)
            self._process = None
            self._reader = None
            self._stdout_thread = None
            self._stderr_thread = None

        logger.info("Stopped iaprof", exit_code=process.returncode, stderr=self.stderr)

    def _stop_after_failure(self) -> None:
        try:
            self.stop()
        except Exception:
            logger.exception("Failed to stop iaprof after failure")

    def is_running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def restart(self) -> None:
        self.stop()
        self.start()

    def restart_if_not_running(self) -> None:
        if not self.is_running():
            logger.warning(
                "iaprof not running (unexpectedly), restarting... "
                "kernels loaded before the restart may not be attributed"
            )
            self.restart()

    @property
    def stderr(self) -> str:
        with self._stderr_lock:
            return "".join(self._stderr_lines)

    def _command(self) -> List[str]:
        return [
            self._path,
            f"--interval={self._interval_ms}",
            f"--eu-stall-subsample={self._eu_stall_subsample}",
        ]

    def _read_stderr(self, lines: TextIO) -> None:
        for line in lines:
            with self._stderr_lock:
                self._stderr_lines.append(line)

    def _join_threads(self) -> None:
        threads = (self._stdout_thread, self._stderr_thread)
        for thread in threads:
            if thread is not None and thread.ident is not None:
                thread.join(timeout=self._stop_timeout)

        if any(thread is not None and thread.is_alive() for thread in threads):
            raise IaprofProcessError("iaprof output readers failed to stop")

    def _error_message(self, message: str) -> str:
        stderr = self.stderr.strip()
        return f"{message}: {stderr}" if stderr else message
