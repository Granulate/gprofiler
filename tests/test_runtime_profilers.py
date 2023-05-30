#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import sys
from collections import defaultdict
from typing import Any, Dict, Iterable, List, cast

import pytest
from pytest import MonkeyPatch

from gprofiler import profilers
from gprofiler.profilers import registry
from gprofiler.profilers.factory import get_profilers
from gprofiler.profilers.profiler_base import NoopProfiler, ProfilerBase
from gprofiler.profilers.registry import ProfilerConfig, register_profiler


def _register_mock_profiler(
    runtime: str,
    profiler_name: str,
    profiler_class: Any = NoopProfiler,
    is_preferred: bool = False,
    default_mode: str = "disabled",
    possible_modes: List[str] = ["disabled"],
    supported_archs: List[str] = ["x86_64"],
    supported_profiling_modes: List[str] = ["cpu"],
) -> None:
    register_profiler(
        runtime=runtime,
        profiler_name=profiler_name,
        is_preferred=is_preferred,
        default_mode=default_mode,
        possible_modes=possible_modes,
        supported_archs=supported_archs,
        supported_profiling_modes=supported_profiling_modes,
    )(profiler_class)


def test_profiler_names_are_unique(monkeypatch: MonkeyPatch) -> None:
    """
    Make sure that registered profilers are checked for uniqueness.
    """
    # register mock class under the same profiler name but for different runtimes;
    # define mock modes, to let registration complete.
    with monkeypatch.context() as m:
        # clear registry before registering mock profilers
        m.setattr(profilers.registry, "profilers_config", defaultdict(list))
        _register_mock_profiler("python", profiler_name="mock", possible_modes=["mock-profiler", "disabled"])
        with pytest.raises(AssertionError) as excinfo:
            _register_mock_profiler("ruby", profiler_name="mock", possible_modes=["mock-spy", "disabled"])
        assert "mock is already registered" in str(excinfo.value)


def _copy_of_registry(keys: Iterable[str] = []) -> Dict[str, List[ProfilerConfig]]:
    return defaultdict(list, {k: v[:] for (k, v) in registry.profilers_config.items() if k in keys})


class MockProfiler(ProfilerBase):
    def __init__(self, mock_mode: str = "", *args: Any, **kwargs: Any):
        logging.warning(f"MOCKINIT/ args={list(*args)}, kwargs={dict(**kwargs)}")
        self.mock_mode = mock_mode


@pytest.mark.parametrize("profiler_mode", ["mock-perf", "mock-profiler", "disabled"])
def test_union_of_runtime_profilers_modes(
    profiler_mode: str,
    monkeypatch: MonkeyPatch,
) -> None:
    """
    Test that generated command line arguments allow union of modes from all profilers for a runtime.
    """
    with monkeypatch.context() as m:
        # clear registry before registering mock profilers;
        # keep Perf profiler, as some of its arguments (perf_dwarf_stack_size) are checked in command line parsing
        m.setattr(profilers.registry, "profilers_config", _copy_of_registry(keys=["Perf"]))
        _register_mock_profiler(
            "mock",
            profiler_name="mock-profiler",
            profiler_class=MockProfiler,
            possible_modes=["mock-profiler", "disabled"],
        )
        MockPerf = type("MockPerf", (MockProfiler,), dict(**MockProfiler.__dict__))
        _register_mock_profiler(
            "mock", profiler_name="mock-perf", profiler_class=MockPerf, possible_modes=["mock-perf", "disabled"]
        )
        from gprofiler.main import parse_cmd_args

        # replace command-line args to include mode we're targeting
        m.setattr(
            sys,
            "argv",
            sys.argv[:1]
            + [
                "--output-dir",
                "./",
                "--mock-mode",
                profiler_mode,
            ],
        )
        args = parse_cmd_args()
        assert args.mock_mode == profiler_mode


@pytest.mark.parametrize("profiler_mode", ["mock-perf", "mock-profiler"])
def test_select_specific_runtime_profiler(
    profiler_mode: str,
    monkeypatch: MonkeyPatch,
) -> None:
    with monkeypatch.context() as m:
        # clear registry before registering mock profilers
        m.setattr(profilers.registry, "profilers_config", _copy_of_registry(keys=["Perf"]))
        _register_mock_profiler(
            "mock",
            profiler_name="mock-profiler",
            profiler_class=MockProfiler,
            possible_modes=["mock-profiler", "disabled"],
        )
        MockPerf = type("MockPerf", (MockProfiler,), dict(**MockProfiler.__dict__))

        _register_mock_profiler(
            "mock", profiler_name="mock-perf", profiler_class=MockPerf, possible_modes=["mock-perf", "disabled"]
        )
        print(registry.profilers_config)
        from gprofiler.main import parse_cmd_args

        # replace command-line args to include mode we're targeting
        m.setattr(
            # sys, "argv", sys.argv[:1] + ["--output-dir", "./", f"--{profiler_mode}-mode", profiler_mode, "--no-perf"]
            sys,
            "argv",
            sys.argv[:1] + ["--output-dir", "./", "--mock-mode", profiler_mode, "--no-perf"],
        )
        args = parse_cmd_args()
        _, process_profilers = get_profilers(args.__dict__)
        assert len(process_profilers) == 1
        profiler = process_profilers[0]
        assert profiler.__class__.__name__ == {"mock-perf": "MockPerf", "mock-profiler": "MockProfiler"}[profiler_mode]
        assert cast(MockProfiler, profiler).mock_mode == profiler_mode


@pytest.mark.parametrize("preferred_profiler", ["mock-perf", "mock-profiler"])
def test_auto_select_preferred_profiler(
    preferred_profiler: str,
    monkeypatch: MonkeyPatch,
) -> None:
    """
    Test that auto selection mechanism correctly selects one of profilers.
    """
    with monkeypatch.context() as m:
        # clear registry before registering mock profilers
        m.setattr(profilers.registry, "profilers_config", _copy_of_registry(keys=["Perf"]))
        _register_mock_profiler(
            "mock",
            profiler_name="mock-profiler",
            profiler_class=MockProfiler,
            is_preferred="mock-profiler" == preferred_profiler,
            possible_modes=["mock-profiler", "disabled"],
        )
        MockPerf = type("MockPerf", (MockProfiler,), dict(**MockProfiler.__dict__))

        _register_mock_profiler(
            "mock",
            profiler_name="mock-perf",
            profiler_class=MockPerf,
            is_preferred="mock-perf" == preferred_profiler,
            possible_modes=["mock-perf", "disabled"],
        )
        from gprofiler.main import parse_cmd_args

        m.setattr(
            sys,
            "argv",
            sys.argv[:1] + ["--output-dir", "./", "--mock-mode", "enabled", "--no-perf"],
        )
        args = parse_cmd_args()
        _, process_profilers = get_profilers(args.__dict__)
        assert len(process_profilers) == 1
        profiler = process_profilers[0]
        assert (
            profiler.__class__.__name__
            == {"mock-perf": "MockPerf", "mock-profiler": "MockProfiler"}[preferred_profiler]
        )
        assert cast(MockProfiler, profiler).mock_mode == "enabled"
