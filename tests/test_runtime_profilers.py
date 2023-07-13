#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import sys
from collections import defaultdict
from typing import Any, Dict, Iterable, List, Optional, Set, Type, cast

import pytest
from pytest import MonkeyPatch

from gprofiler import profilers
from gprofiler.gprofiler_types import ProcessToProfileData
from gprofiler.profilers import registry
from gprofiler.profilers.factory import get_profilers
from gprofiler.profilers.perf import PerfRuntime
from gprofiler.profilers.profiler_base import NoopProfiler, ProfilerBase
from gprofiler.profilers.registry import (
    ProfilerArgument,
    ProfilerConfig,
    ProfilingRuntime,
    RuntimeConfig,
    register_profiler,
    register_runtime,
)
from tests.utils import get_arch


class MockRuntime(ProfilingRuntime):
    pass


def _register_mock_runtime(
    runtime_name: str,
    default_mode: str = "enabled",
    runtime_class: Any = MockRuntime,
    common_arguments: Optional[List[ProfilerArgument]] = None,
) -> None:
    register_runtime(runtime_name, default_mode, common_arguments=common_arguments)(runtime_class)


def _register_mock_profiler(
    profiler_name: str,
    runtime_class: Any,
    profiler_class: Any = NoopProfiler,
    is_preferred: bool = False,
    possible_modes: List[str] = ["disabled"],
    supported_archs: List[str] = ["x86_64"],
    supported_profiling_modes: List[str] = ["cpu"],
    profiler_arguments: Optional[List[ProfilerArgument]] = None,
) -> None:
    register_profiler(
        profiler_name=profiler_name,
        runtime_class=runtime_class,
        is_preferred=is_preferred,
        possible_modes=possible_modes,
        supported_archs=supported_archs,
        supported_profiling_modes=supported_profiling_modes,
        profiler_arguments=profiler_arguments,
    )(profiler_class)


def _subset_of_profilers(
    keys: Iterable[Type[ProfilingRuntime]] = [],
) -> Dict[Type[ProfilingRuntime], List[ProfilerConfig]]:
    return defaultdict(list, {k: v[:] for (k, v) in registry.profilers_config.items() if k in keys})


def _subset_of_runtimes(keys: Iterable[Type[ProfilingRuntime]] = []) -> Dict[Type[ProfilingRuntime], RuntimeConfig]:
    return {k: v for (k, v) in registry.runtimes_config.items() if k in keys}


def test_profiler_names_are_unique(monkeypatch: MonkeyPatch) -> None:
    """
    Make sure that registered profilers are checked for uniqueness.
    """
    # register mock class under the same profiler name but for different runtimes;
    # define mock modes, to let registration complete.
    with monkeypatch.context() as m:
        # clear registry before registering mock profilers
        m.setattr(profilers.registry, "profilers_config", defaultdict(list))
        m.setattr(profilers.registry, "runtimes_config", _subset_of_runtimes(keys=[PerfRuntime]))
        _register_mock_runtime("mock", runtime_class=MockRuntime)

        _register_mock_profiler(
            profiler_name="mock", runtime_class=MockRuntime, possible_modes=["mock-profiler", "disabled"]
        )
        with pytest.raises(AssertionError) as excinfo:
            _register_mock_profiler(
                profiler_name="mock", runtime_class=PerfRuntime, possible_modes=["mock-spy", "disabled"]
            )
        assert "mock is already registered" in str(excinfo.value)


class MockProfiler(ProfilerBase):
    def __init__(self, mock_mode: str = "", *args: Any, **kwargs: Any):
        self.mock_mode = mock_mode
        self.kwargs = dict(**kwargs)

    def snapshot(self) -> ProcessToProfileData:
        return {}


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
        m.setattr(profilers.registry, "profilers_config", _subset_of_profilers(keys=[PerfRuntime]))
        m.setattr(profilers.registry, "runtimes_config", _subset_of_runtimes(keys=[PerfRuntime]))
        _register_mock_runtime("mock", runtime_class=MockRuntime)
        _register_mock_profiler(
            profiler_name="mock-profiler",
            runtime_class=MockRuntime,
            profiler_class=MockProfiler,
            possible_modes=["mock-profiler", "disabled"],
        )
        MockPerf = type("MockPerf", (MockProfiler,), dict(**MockProfiler.__dict__))
        _register_mock_profiler(
            profiler_name="mock-perf",
            runtime_class=MockRuntime,
            profiler_class=MockPerf,
            possible_modes=["mock-perf", "disabled"],
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
        m.setattr(profilers.registry, "profilers_config", _subset_of_profilers(keys=[PerfRuntime]))
        m.setattr(profilers.registry, "runtimes_config", _subset_of_runtimes(keys=[PerfRuntime]))
        _register_mock_runtime("mock", runtime_class=MockRuntime)
        _register_mock_profiler(
            profiler_name="mock-profiler",
            runtime_class=MockRuntime,
            profiler_class=MockProfiler,
            possible_modes=["mock-profiler", "disabled"],
            supported_archs=[get_arch()],
        )
        MockPerf = type("MockPerf", (MockProfiler,), dict(**MockProfiler.__dict__))

        _register_mock_profiler(
            profiler_name="mock-perf",
            runtime_class=MockRuntime,
            profiler_class=MockPerf,
            possible_modes=["mock-perf", "disabled"],
            supported_archs=[get_arch()],
        )
        from gprofiler.main import parse_cmd_args

        # replace command-line args to include mode we're targeting
        m.setattr(
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
        m.setattr(profilers.registry, "profilers_config", _subset_of_profilers(keys=[PerfRuntime]))
        m.setattr(profilers.registry, "runtimes_config", _subset_of_runtimes(keys=[PerfRuntime]))
        _register_mock_runtime("mock", runtime_class=MockRuntime)
        _register_mock_profiler(
            profiler_name="mock-profiler",
            runtime_class=MockRuntime,
            profiler_class=MockProfiler,
            is_preferred="mock-profiler" == preferred_profiler,
            possible_modes=["mock-profiler", "disabled"],
            supported_archs=[get_arch()],
        )
        MockPerf = type("MockPerf", (MockProfiler,), dict(**MockProfiler.__dict__))

        _register_mock_profiler(
            profiler_name="mock-perf",
            runtime_class=MockRuntime,
            profiler_class=MockPerf,
            is_preferred="mock-perf" == preferred_profiler,
            possible_modes=["mock-perf", "disabled"],
            supported_archs=[get_arch()],
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


@pytest.mark.parametrize(
    "preferred_profiler,expected_args,unwanted_args",
    [
        pytest.param(
            "mock-perf",
            {"duration", "frequency", "mock_one", "mock_two", "mock_mock_perf_two"},
            {"mock_mock_profiler_one"},
            id="mock_perf",
        ),
        pytest.param(
            "mock-profiler",
            {"duration", "frequency", "mock_one", "mock_two", "mock_mock_profiler_one"},
            {"mock_mock_perf_two"},
            id="mock_profiler",
        ),
    ],
)
def test_assign_correct_profiler_arguments(
    monkeypatch: MonkeyPatch,
    preferred_profiler: str,
    expected_args: Set[str],
    unwanted_args: Set[str],
) -> None:
    """
    Test that selected profiler gets all of its own or common arguments, none from other profiler.
    """
    with monkeypatch.context() as m:
        # clear registry before registering mock profilers
        m.setattr(profilers.registry, "profilers_config", _subset_of_profilers(keys=[PerfRuntime]))
        m.setattr(profilers.registry, "runtimes_config", _subset_of_runtimes(keys=[PerfRuntime]))
        _register_mock_runtime(
            "mock",
            runtime_class=MockRuntime,
            common_arguments=[
                ProfilerArgument("--mock-common-one", "mock_one"),
                ProfilerArgument("--mock-common-two", "mock_two"),
            ],
        )
        _register_mock_profiler(
            profiler_name="mock-profiler",
            runtime_class=MockRuntime,
            profiler_class=MockProfiler,
            is_preferred="mock-profiler" == preferred_profiler,
            possible_modes=["mock-profiler", "disabled"],
            profiler_arguments=[
                ProfilerArgument("--mock-mock-profiler-one", "mock_mock_profiler_one"),
            ],
            supported_archs=[get_arch()],
        )
        MockPerf = type("MockPerf", (MockProfiler,), dict(**MockProfiler.__dict__))

        _register_mock_profiler(
            profiler_name="mock-perf",
            runtime_class=MockRuntime,
            profiler_class=MockPerf,
            is_preferred="mock-perf" == preferred_profiler,
            possible_modes=["mock-perf", "disabled"],
            profiler_arguments=[
                ProfilerArgument("--mock-mock-perf-two", "mock_mock_perf_two"),
            ],
            supported_archs=[get_arch()],
        )
        from gprofiler.main import parse_cmd_args

        m.setattr(
            sys,
            "argv",
            sys.argv[:1]
            + [
                "--output-dir",
                "./",
                "--mock-mode",
                "enabled",
                "--no-perf",
                "--mock-common-one=check",
                "--mock-common-two=check",
                "--mock-mock-profiler-one=check",
                "--mock-mock-perf-two=check",
            ],
        )
        args = parse_cmd_args()
        _, process_profilers = get_profilers(args.__dict__)
        assert len(process_profilers) == 1
        profiler = process_profilers[0]
        mock = cast(MockProfiler, profiler)
        assert set(mock.kwargs.keys()).intersection(unwanted_args) == set()
        assert set(mock.kwargs.keys()) == expected_args
