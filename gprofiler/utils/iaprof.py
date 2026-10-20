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

from collections import Counter, defaultdict
from concurrent.futures import Future
from dataclasses import dataclass
from threading import Lock
from typing import Dict, Iterable, List, Optional, Tuple

from gprofiler.gprofiler_types import ProcessToStackSampleCounters
from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)

STALL_REASONS = (
    "active",
    "control",
    "pipestall",
    "send",
    "dist_acc",
    "sbid",
    "sync",
    "inst_fetch",
    "other",
    "tdr",
)


@dataclass(frozen=True)
class _Kernel:
    pid: int
    frames: Tuple[str, ...]


class IaprofParser:
    def __init__(self) -> None:
        self._strings: Dict[int, str] = {}
        self._kernel: Optional[_Kernel] = None
        self._profiles: ProcessToStackSampleCounters = defaultdict(Counter)
        self._bad_line_count = 0
        self._bad_lines: List[str] = []

    def parse_line(self, line: str) -> Optional[int]:
        line = line.rstrip("\n")
        if not line:
            return None

        record_type = line.partition("\t")[0]
        if record_type == "interval":
            self._kernel = None
            try:
                return self._parse_interval(line)
            except ValueError:
                self._record_bad_line(line)
            return None

        try:
            if record_type == "string":
                self._parse_string(line)
            elif record_type == "kernel":
                self._kernel = None
                self._parse_kernel(line)
            elif record_type == "eustall":
                self._parse_eustall(line)
            elif record_type == "metric":
                self._parse_metric(line)
            else:
                raise ValueError
        except (KeyError, ValueError):
            self._record_bad_line(line)

        return None

    def take_snapshot(self) -> ProcessToStackSampleCounters:
        profiles = self._profiles
        self._profiles = defaultdict(Counter)

        if self._bad_line_count:
            logger.warning(
                f"Skipped {self._bad_line_count} malformed iaprof records (showing up to 8):\n"
                + "\n".join(self._bad_lines)
            )
        self._bad_line_count = 0
        self._bad_lines = []
        return profiles

    def _parse_string(self, line: str) -> None:
        _, string_id, value = line.split("\t", maxsplit=2)
        parsed_id = int(string_id)
        if parsed_id <= 0:
            raise ValueError
        self._strings[parsed_id] = value

    def _parse_interval(self, line: str) -> int:
        _, interval, timestamp = line.split("\t")
        parsed_interval = int(interval)
        float(timestamp)
        return parsed_interval

    def _parse_metric(self, line: str) -> None:
        _, _, value = line.split("\t")
        float(value)

    def _parse_kernel(self, line: str) -> None:
        _, gpu_address, comm_id, pid, cpu_stack_id, source_id, symbol_id = line.split("\t")
        int(gpu_address, 0)
        parsed_pid = int(pid)
        if parsed_pid <= 0:
            raise ValueError

        cpu_frames = tuple(
            self._frame(frame, "[unknown]") for frame in self._string_value(cpu_stack_id).split(";") if frame
        )
        frames = (
            self._frame(self._string_value(comm_id), "[unknown]"),
            *cpu_frames,
            "-",
            f"{self._frame(self._string_value(source_id), '[unknown]')}_[G]",
            f"{self._frame(self._string_value(symbol_id), '[unknown]')}_[G]",
        )
        self._kernel = _Kernel(parsed_pid, frames)

    def _parse_eustall(self, line: str) -> None:
        if self._kernel is None:
            raise ValueError

        fields = line.split("\t")
        if len(fields) != 13:
            raise ValueError
        offset = fields[1]
        int(offset, 0)
        instruction = self._frame(self._string_value(fields[2]), "[unknown]")
        counts = tuple(int(value) for value in fields[3:])
        if any(count < 0 for count in counts):
            raise ValueError

        for reason, count in zip(STALL_REASONS, counts):
            if count:
                stack = ";".join((*self._kernel.frames, f"{instruction}_[g]", f"{reason}_[g]", f"{offset}_[g]"))
                self._profiles[self._kernel.pid][stack] += count

    def _string_value(self, string_id: str) -> str:
        parsed_id = int(string_id)
        if parsed_id == 0:
            return ""
        return self._strings[parsed_id]

    @staticmethod
    def _frame(value: str, fallback: str) -> str:
        return value.strip().replace(";", "|") or fallback

    def _record_bad_line(self, line: str) -> None:
        self._bad_line_count += 1
        if len(self._bad_lines) < 8:
            self._bad_lines.append(line)


class IaprofOutputReader:
    def __init__(self) -> None:
        self._parser = IaprofParser()
        self._lock = Lock()
        self._ready: Future[int] = Future()
        self._snapshot: Optional[Future[ProcessToStackSampleCounters]] = None
        self._error: Optional[BaseException] = None

    def read(self, lines: Iterable[str]) -> None:
        try:
            for line in lines:
                interval = self._parser.parse_line(line)
                if interval is not None:
                    if not self._ready.done():
                        self._ready.set_result(interval)
                    self._publish_snapshot()
        except BaseException as error:
            self._finish(error)
        else:
            self._finish(EOFError("iaprof output closed"))

    def wait_until_ready(self, timeout: float) -> int:
        return self._ready.result(timeout)

    def request_snapshot(self) -> Future[ProcessToStackSampleCounters]:
        snapshot: Future[ProcessToStackSampleCounters] = Future()
        with self._lock:
            if self._snapshot is not None:
                raise RuntimeError("iaprof snapshot already requested")
            if self._error is None:
                self._snapshot = snapshot
            else:
                snapshot.set_exception(self._error)
        return snapshot

    def _publish_snapshot(self) -> None:
        with self._lock:
            if self._snapshot is None:
                return
            profiles = self._parser.take_snapshot()
            snapshot = self._snapshot
            self._snapshot = None
        snapshot.set_result(profiles)

    def _finish(self, error: BaseException) -> None:
        with self._lock:
            self._error = error
            snapshot = self._snapshot
            self._snapshot = None
        if not self._ready.done():
            self._ready.set_exception(error)
        if snapshot is not None and not snapshot.done():
            snapshot.set_exception(error)
