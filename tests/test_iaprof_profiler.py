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

import sys
from collections import Counter
from pathlib import Path
from subprocess import CompletedProcess
from typing import Optional, Tuple
from unittest.mock import Mock, patch

import pytest
from packaging.version import InvalidVersion

import gprofiler.profilers.iaprof as iaprof
from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import UserArgs
from gprofiler.main import parse_cmd_args
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.factory import get_profilers
from gprofiler.profilers.iaprof import IAPROF_SNAPSHOT_TIMEOUT, IaprofProfiler, frequency_to_iaprof_interval
from gprofiler.utils.iaprof_process import IaprofProcessError


def _make_profiler(profiler_state: ProfilerState) -> Tuple[IaprofProfiler, Mock]:
    profiler_state.profiling_mode = "gpu"
    with patch.object(iaprof, "IaprofProcess") as process_class:
        profiler = IaprofProfiler(100, 0, profiler_state, "enabled", "/opt/iaprof", 100)
    process_class.assert_called_once_with("/opt/iaprof", 10, 100)
    return profiler, process_class.return_value


def test_iaprof_profiler_lifecycle(profiler_state: ProfilerState) -> None:
    profiler, process = _make_profiler(profiler_state)

    with patch.object(profiler, "_get_iaprof_version", return_value="2026.3") as get_version:
        profiler.start()
    profiler.stop()

    get_version.assert_called_once_with()

    process.start.assert_called_once_with()
    process.stop.assert_called_once_with()


def test_iaprof_profiler_metadata(profiler_state: ProfilerState) -> None:
    profiler, process = _make_profiler(profiler_state)

    with patch.object(profiler, "_get_iaprof_version", return_value="2026.3"):
        profiler.start()

    assert profiler.get_profile_metadata() == {
        "iaprof_version": "2026.3",
        "iaprof_output_interval_ms": 10,
        "iaprof_eu_stall_subsample": 100,
    }
    process.start.assert_called_once_with()


def test_iaprof_profiler_version(profiler_state: ProfilerState) -> None:
    profiler, _ = _make_profiler(profiler_state)
    result = CompletedProcess(["/opt/iaprof", "--version"], 0, b"", b"2026.3\n")

    with patch.object(iaprof, "run_process", return_value=result) as run_process:
        version = profiler._get_iaprof_version()

    assert version == "2026.3"
    run_process.assert_called_once_with(
        ["/opt/iaprof", "--version"],
        stop_event=profiler_state.stop_event,
        timeout=5,
        pdeathsigger=False,
    )


def test_iaprof_profiler_rejects_old_version(profiler_state: ProfilerState) -> None:
    profiler, _ = _make_profiler(profiler_state)
    result = CompletedProcess(["/opt/iaprof", "--version"], 0, b"", b"2026.2\n")

    with patch.object(iaprof, "run_process", return_value=result), pytest.raises(
        RuntimeError, match="iaprof 2026.2 is unsupported; minimum version is 2026.3"
    ):
        profiler._get_iaprof_version()


def test_iaprof_profiler_rejects_invalid_version(profiler_state: ProfilerState) -> None:
    profiler, _ = _make_profiler(profiler_state)
    result = CompletedProcess(["/opt/iaprof", "--version"], 0, b"", b"invalid\n")

    with patch.object(iaprof, "run_process", return_value=result), pytest.raises(InvalidVersion):
        profiler._get_iaprof_version()


def test_iaprof_gpu_mode_rejects_rootless(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "argv", ["gprofiler", "--mode=gpu", "--rootless", "-o", str(tmp_path)])

    with pytest.raises(SystemExit):
        parse_cmd_args()


def test_iaprof_profiler_snapshot(profiler_state: ProfilerState) -> None:
    profiler, process = _make_profiler(profiler_state)
    stacks = Counter({"app;-;source.cpp_[G];kernel_[G];mov_[g];active_[g];0x10_[g]": 7})
    process.snapshot.return_value = {42: stacks}

    profiles = profiler.snapshot()

    process.snapshot.assert_called_once_with(IAPROF_SNAPSHOT_TIMEOUT)
    assert profiles[42].stacks is stacks
    assert profiles[42].appid is None
    assert profiles[42].app_metadata is None
    assert profiles[42].container_name == ""


def test_iaprof_profiler_snapshot_stops_with_gprofiler(profiler_state: ProfilerState) -> None:
    profiler, process = _make_profiler(profiler_state)
    profiler_state.stop_event.set()

    with pytest.raises(StopEventSetException):
        profiler.snapshot()

    process.snapshot.assert_not_called()


def test_iaprof_profiler_snapshot_failure_during_stop(profiler_state: ProfilerState) -> None:
    profiler, process = _make_profiler(profiler_state)

    def interrupted_snapshot(timeout: float) -> None:
        profiler_state.stop_event.set()
        raise IaprofProcessError("iaprof snapshot failed")

    process.snapshot.side_effect = interrupted_snapshot

    with pytest.raises(StopEventSetException):
        profiler.snapshot()


def test_iaprof_profiler_selected_for_gpu_mode(profiler_state: ProfilerState) -> None:
    profiler_state.profiling_mode = "gpu"
    user_args: UserArgs = {
        "profiling_mode": "gpu",
        "frequency": 100,
        "duration": 0,
        "min_duration": 0,
        "iaprof_mode": "enabled",
        "iaprof_path": "/opt/iaprof",
        "iaprof_eu_stall_subsample": 100,
    }

    system_profiler, process_profilers = get_profilers(user_args, profiler_state=profiler_state)

    assert isinstance(system_profiler, IaprofProfiler)
    assert process_profilers == []


@pytest.mark.parametrize(
    "frequency,interval_ms",
    [(1, 1000), (11, 91), (100, 10), (1000, 1)],
)
def test_frequency_to_iaprof_interval(frequency: int, interval_ms: int) -> None:
    assert frequency_to_iaprof_interval(frequency) == interval_ms


@pytest.mark.parametrize("frequency", [0, 1001])
def test_frequency_to_iaprof_interval_rejects_out_of_range(frequency: int) -> None:
    with pytest.raises(ValueError):
        frequency_to_iaprof_interval(frequency)


@pytest.mark.parametrize("frequency", [None, 20])
def test_iaprof_gpu_mode_args(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    frequency: Optional[int],
) -> None:
    frequency_args = [] if frequency is None else ["-f", str(frequency)]
    monkeypatch.setattr(sys, "argv", ["gprofiler", "--mode=gpu", "-o", str(tmp_path), *frequency_args])

    args = parse_cmd_args()

    assert args.profiling_mode == "gpu"
    assert args.frequency == (100 if frequency is None else frequency)
    assert args.iaprof_mode == "enabled"
    assert args.iaprof_path == "iaprof"
    assert args.iaprof_eu_stall_subsample == 100


@pytest.mark.parametrize(
    "option",
    [("--alloc-interval", "2mb"), ("--perf-event", "cycles"), ("-f", "1001")],
)
def test_iaprof_gpu_mode_rejects_invalid_options(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, option: Tuple[str, str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["gprofiler", "--mode=gpu", "-o", str(tmp_path), *option])

    with pytest.raises(SystemExit):
        parse_cmd_args()
