#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
from contextlib import _GeneratorContextManager
from pathlib import Path
from threading import Event
from typing import Callable, List

import pytest
from docker import DockerClient
from docker.models.images import Image

from gprofiler.merge import parse_one_collapsed
from gprofiler.profilers.perf import SystemProfiler
from tests import CONTAINERS_DIRECTORY
from tests.conftest import AssertInCollapsed
from tests.utils import assert_function_in_collapsed, run_gprofiler_in_container_for_one_session, snapshot_pid_collapsed


@pytest.mark.parametrize("profiler_type", ["attach-maps"])
@pytest.mark.parametrize("runtime", ["nodejs"])
@pytest.mark.parametrize("application_image_tag", ["without-flags"])
@pytest.mark.parametrize("command_line", [["node", f"{CONTAINERS_DIRECTORY}/nodejs/fibonacci.js"]])
def test_nodejs_attach_maps(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    profiler_type: str,
    command_line: List[str],
    runtime_specific_args: List[str],
) -> None:
    with SystemProfiler(
        1000,
        6,
        Event(),
        str(tmp_path),
        False,
        False,
        perf_mode="fp",
        perf_inject=False,
        perf_dwarf_stack_size=0,
        perf_node_attach=True,
    ) as profiler:
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)
        # check for node built-in functions
        assert_function_in_collapsed("node::Start", process_collapsed)
        # check for v8 built-in functions
        assert_function_in_collapsed("v8::Function::Call", process_collapsed)


@pytest.mark.parametrize("profiler_type", ["attach-maps"])
@pytest.mark.parametrize("runtime", ["nodejs"])
@pytest.mark.parametrize("application_image_tag", ["without-flags"])
@pytest.mark.parametrize("command_line", [["node", f"{CONTAINERS_DIRECTORY}/nodejs/fibonacci.js"]])
def test_nodejs_attach_maps_from_container(
    docker_client: DockerClient,
    application_pid: int,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    assert_collapsed: AssertInCollapsed,
    profiler_flags: List[str],
) -> None:
    _ = application_pid  # Fixture only used for running the application.
    collapsed_text = run_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, output_directory, output_collapsed, runtime_specific_args, profiler_flags
    )
    collapsed = parse_one_collapsed(collapsed_text)
    assert_collapsed(collapsed)


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("profiler_type", ["attach-maps"])
@pytest.mark.parametrize("runtime", ["nodejs"])
@pytest.mark.parametrize("application_image_tag", ["without-flags"])
def test_twoprocesses_nodejs_attach_maps(
    tmp_path: Path,
    assert_collapsed: AssertInCollapsed,
    profiler_type: str,
    profiler_flags: List[str],
    application_factory: Callable[[], _GeneratorContextManager],
) -> None:
    with application_factory() as pid1:
        with application_factory() as pid2:
            with SystemProfiler(
                1000,
                6,
                Event(),
                str(tmp_path),
                False,
                False,
                perf_mode="fp",
                perf_inject=False,
                perf_dwarf_stack_size=0,
                perf_node_attach=True,
            ) as profiler:
                results = profiler.snapshot()
                assert_collapsed(results[pid1].stacks)
                assert_collapsed(results[pid2].stacks)


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize(
    "application_image_tag",
    [
        "10-glibc",
        "10-musl",
        "11-glibc",
        "11-musl",
        "12-glibc",
        "12-musl",
        "13-glibc",
        "13-musl",
        "14-glibc",
        "14-musl",
        "15-glibc",
        "15-musl",
        "16-glibc",
        "16-musl",
    ],
)
@pytest.mark.parametrize("profiler_type", ["attach-maps"])
@pytest.mark.parametrize("runtime", ["nodejs"])
def test_nodejs_matrix(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    runtime_specific_args: List[str],
    profiler_flags: List[str],
    application_image_tag: str,
) -> None:
    with SystemProfiler(
        1000,
        6,
        Event(),
        str(tmp_path),
        False,
        False,
        perf_mode="fp",
        perf_inject=False,
        perf_dwarf_stack_size=0,
        perf_node_attach=True,
    ) as profiler:
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)
