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

import subprocess
import sys
from pathlib import Path
from time import monotonic
from typing import Any, List

import pytest
from pytest import MonkeyPatch

import gprofiler.utils.iaprof_process as iaprof_process
from gprofiler.utils import start_process
from gprofiler.utils.iaprof_process import IaprofProcess, IaprofProcessError


def _write_executable(path: Path, source: str) -> None:
    path.write_text(f"#!{sys.executable}\n{source}")
    path.chmod(0o755)


def _disable_pdeathsigger(monkeypatch: MonkeyPatch) -> None:
    def start_without_pdeathsigger(command: List[str], **kwargs: Any) -> subprocess.Popen[str]:
        return start_process(command, pdeathsigger=False, **kwargs)

    monkeypatch.setattr(iaprof_process, "start_process", start_without_pdeathsigger)


def test_iaprof_process_snapshot_and_stop(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _disable_pdeathsigger(monkeypatch)
    executable = tmp_path / "fake-iaprof"
    _write_executable(
        executable,
        """
import signal
import sys
import time

running = True

def stop(*args):
    global running
    running = False

signal.signal(signal.SIGINT, stop)
print("interval\\t0\\t1.0", flush=True)
print("string\\t1\\tapp")
print("string\\t2\\tkernel")
print("string\\t3\\tmov")
interval = 1
while running:
    print("kernel\\t0x1000\\t1\\t42\\t0\\t0\\t2")
    print("eustall\\t0x10\\t3\\t1\\t0\\t0\\t0\\t0\\t0\\t0\\t0\\t0\\t0")
    print(f"interval\\t{interval}\\t2.0", flush=True)
    interval += 1
    time.sleep(0.01)
print("stopped", file=sys.stderr, flush=True)
""",
    )
    process = IaprofProcess(str(executable), 10, 100, startup_timeout=2, stop_timeout=1)

    try:
        process.start()
        profiles = process.snapshot(timeout=2)
        stack = "app;-;[unknown]_[G];kernel_[G];mov_[g];active_[g];0x10_[g]"
        assert profiles[42][stack] >= 1
    finally:
        process.stop()

    assert not process.is_running()
    assert "stopped" in process.stderr


def test_iaprof_process_start_failure(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _disable_pdeathsigger(monkeypatch)
    executable = tmp_path / "fake-iaprof"
    _write_executable(
        executable,
        """
import sys

print("GPU unavailable", file=sys.stderr, flush=True)
raise SystemExit(2)
""",
    )
    process = IaprofProcess(str(executable), 10, 100, startup_timeout=2, stop_timeout=1)

    with pytest.raises(IaprofProcessError, match="GPU unavailable"):
        process.start()
    assert not process.is_running()


def test_iaprof_process_kills_after_stop_timeout(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _disable_pdeathsigger(monkeypatch)
    executable = tmp_path / "fake-iaprof"
    _write_executable(
        executable,
        """
import signal
import time

signal.signal(signal.SIGINT, signal.SIG_IGN)
print("interval\\t0\\t1.0", flush=True)
while True:
    time.sleep(1)
""",
    )
    process = IaprofProcess(str(executable), 10, 100, startup_timeout=2, stop_timeout=0.1)
    process.start()

    start = monotonic()
    process.stop()

    assert monotonic() - start < 1
    assert not process.is_running()


def test_iaprof_process_snapshot_timeout_kills_child(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _disable_pdeathsigger(monkeypatch)
    executable = tmp_path / "iaprof"
    _write_executable(
        executable,
        """
import time

print("interval\\t0\\t1.0", flush=True)
while True:
    time.sleep(1)
""",
    )

    process = IaprofProcess(
        str(executable),
        10,
        100,
        startup_timeout=2,
        stop_timeout=1,
    )
    process.start()

    with pytest.raises(IaprofProcessError, match="iaprof snapshot timed out"):
        process.snapshot(timeout=0.5)

    assert not process.is_running()
    process.stop()


def test_iaprof_process_restarts_after_exit(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    _disable_pdeathsigger(monkeypatch)
    executable = tmp_path / "fake-iaprof"
    marker = tmp_path / "started-once"
    _write_executable(
        executable,
        f"""
import pathlib
import signal
import sys
import time

marker = pathlib.Path({str(marker)!r})
print("interval\\t0\\t1.0", flush=True)
if not marker.exists():
    marker.touch()
    print("device lost", file=sys.stderr, flush=True)
    sys.exit(3)
signal.signal(signal.SIGINT, lambda *args: sys.exit(0))
while True:
    time.sleep(0.01)
    print("interval\\t0\\t1.0", flush=True)
""",
    )
    process = IaprofProcess(str(executable), 10, 100, startup_timeout=2, stop_timeout=1)
    process.start()

    with pytest.raises(IaprofProcessError, match="iaprof snapshot failed"):
        process.snapshot(timeout=2)

    process.restart_if_not_running()
    assert process.is_running()
    assert process.snapshot(timeout=2) == {}
    process.stop()
    assert not process.is_running()
