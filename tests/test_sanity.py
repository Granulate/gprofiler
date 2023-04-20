#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import re
import time
from contextlib import _GeneratorContextManager
from pathlib import Path
from threading import Event
from typing import Any, Callable, List, Optional

import pytest
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.dotnet import DotnetProfiler
from gprofiler.profilers.perf import SystemProfiler
from gprofiler.profilers.php import PHPSpyProfiler
from gprofiler.profilers.python import PySpyProfiler
from gprofiler.profilers.python_ebpf import PythonEbpfProfiler
from gprofiler.profilers.ruby import RbSpyProfiler
from gprofiler.utils import wait_event
from gprofiler.utils.collapsed_format import parse_one_collapsed
from tests import PHPSPY_DURATION
from tests.conftest import AssertInCollapsed
from tests.utils import (
    RUNTIME_PROFILERS,
    assert_function_in_collapsed,
    is_aarch64,
    make_java_profiler,
    run_gprofiler_in_container_for_one_session,
    snapshot_pid_collapsed,
    start_gprofiler_in_container_for_one_session,
    wait_for_gprofiler_container,
)


@pytest.mark.parametrize("runtime", ["java"])
def test_java_from_host(
    tmp_path_world_accessible: Path,
    application_pid: int,
    assert_app_id: Callable,
    assert_collapsed: AssertInCollapsed,
    profiler_state: ProfilerState,
) -> None:
    with make_java_profiler(
        profiler_state,
        frequency=99,
        java_async_profiler_mode="itimer",
    ) as profiler:
        _ = assert_app_id  # Required for mypy unused argument warning
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)


@pytest.mark.parametrize("runtime", ["python"])
def test_pyspy(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    assert_app_id: Callable,
    python_version: Optional[str],
    profiler_state: ProfilerState,
) -> None:
    _ = assert_app_id  # Required for mypy unused argument warning
    with PySpyProfiler(1000, 3, profiler_state, add_versions=True) as profiler:
        # not using snapshot_one_collapsed because there are multiple Python processes running usually.
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)
        assert_function_in_collapsed("PyYAML==6.0", process_collapsed)  # Ensure package info is presented
        # Ensure Python version is presented
        assert python_version is not None, "Failed to find python version"
        assert_function_in_collapsed(f"standard-library=={python_version}", process_collapsed)


@pytest.mark.parametrize("runtime", ["php"])
def test_phpspy(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    in_container: bool,
    profiler_state: ProfilerState,
) -> None:
    if not in_container:
        pytest.skip("Flaky https://github.com/Granulate/gprofiler/issues/630")

    with PHPSpyProfiler(
        1000,
        PHPSPY_DURATION,
        profiler_state,
        php_process_filter="php",
        php_mode="phpspy",
    ) as profiler:
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)


@pytest.mark.parametrize("runtime", ["ruby"])
def test_rbspy(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    gprofiler_docker_image: Image,
    profiler_state: ProfilerState,
) -> None:
    with RbSpyProfiler(1000, 3, profiler_state, "rbspy") as profiler:
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)


@pytest.mark.parametrize("runtime", ["dotnet"])
def test_dotnet_trace(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    gprofiler_docker_image: Image,
    profiler_state: ProfilerState,
) -> None:
    with DotnetProfiler(1000, 3, profiler_state, "dotnet-trace") as profiler:
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)


@pytest.mark.parametrize("runtime", ["nodejs"])
def test_nodejs(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    gprofiler_docker_image: Image,
    profiler_state: ProfilerState,
) -> None:
    with SystemProfiler(
        1000,
        6,
        profiler_state,
        perf_mode="fp",
        perf_inject=True,
        perf_dwarf_stack_size=0,
        perf_node_attach=False,
        perf_memory_restart=False,
    ) as profiler:
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)


@pytest.mark.parametrize("runtime", ["python"])
def test_python_ebpf(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    assert_app_id: Callable,
    gprofiler_docker_image: Image,
    python_version: Optional[str],
    no_kernel_headers: Any,
    profiler_state: ProfilerState,
) -> None:
    if is_aarch64():
        pytest.skip(
            "PyPerf doesn't support aarch64 architecture, see https://github.com/Granulate/gprofiler/issues/499"
        )

    _ = assert_app_id  # Required for mypy unused argument warning
    with PythonEbpfProfiler(1000, 5, profiler_state, add_versions=True, verbose=False) as profiler:
        try:
            process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        except UnicodeDecodeError as e:
            print(repr(e.object))  # print the faulty binary data
            raise
        assert_collapsed(process_collapsed)
        assert_function_in_collapsed("do_syscall_64_[k]", process_collapsed)  # ensure kernels stacks exist
        assert_function_in_collapsed(
            "_PyEval_EvalFrameDefault_[pn]", process_collapsed
        )  # ensure native user stacks exist
        # ensure class name exists for instance methods
        assert_function_in_collapsed("lister.Burner.burner", process_collapsed)
        # ensure class name exists for class methods
        assert_function_in_collapsed("lister.Lister.lister", process_collapsed)
        assert_function_in_collapsed("PyYAML==6.0", process_collapsed)  # ensure package info exists
        # ensure Python version exists
        assert python_version is not None, "Failed to find python version"
        assert_function_in_collapsed(f"standard-library=={python_version}", process_collapsed)


@pytest.mark.parametrize(
    "runtime,profiler_type",
    RUNTIME_PROFILERS,
)
def test_from_container(
    docker_client: DockerClient,
    application_pid: int,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    assert_collapsed: AssertInCollapsed,
    assert_app_id: Callable,
    profiler_flags: List[str],
    runtime: str,
    in_container: bool,
) -> None:
    if runtime == "php" and not in_container:
        pytest.skip("Flaky https://github.com/Granulate/gprofiler/issues/630")

    _ = application_pid  # Fixture only used for running the application.
    _ = assert_app_id  # Required for mypy unused argument warning
    collapsed_text = run_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, output_directory, output_collapsed, runtime_specific_args, profiler_flags
    )
    collapsed = parse_one_collapsed(collapsed_text)
    assert_collapsed(collapsed)


@pytest.mark.parametrize(
    "runtime,profiler_type",
    [
        ("java", "ap"),
        ("python", "py-spy"),
        ("ruby", "rbspy"),
    ],
)
def test_from_container_spawned_process(
    docker_client: DockerClient,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    assert_collapsed: AssertInCollapsed,
    profiler_flags: List[str],
    application_factory: Callable[[], _GeneratorContextManager],
) -> None:
    profiler_flags.extend(["-d", "30", "--profile-spawned-processes"])
    container = start_gprofiler_in_container_for_one_session(
        docker_client,
        gprofiler_docker_image,
        output_directory,
        output_collapsed,
        runtime_specific_args,
        profiler_flags,
    )

    try:
        wait_event(30, Event(), lambda: re.search(rb"selected \d+ processes to profile", container.logs()) is not None)
    except TimeoutError:
        print(container.logs())
        raise

    # We only start the application after gprofiler started its single profiling session. This means that the only way
    # for it to profile the application is by detecting its spawn and start profiling it mid-session.
    with application_factory():
        collapsed_text = wait_for_gprofiler_container(container, output_collapsed)
        collapsed = parse_one_collapsed(collapsed_text)
        assert_collapsed(collapsed)

    assert b"Profiling spawned process" in container.logs()


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("profiler_type", ["py-spy"])
@pytest.mark.parametrize("runtime", ["python"])
def test_container_name_when_stopped(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    runtime_specific_args: List[str],
    profiler_type: str,
    profiler_flags: List[str],
    application_docker_container: Container,
) -> None:
    """
    Tests that container name is added to data even when container stops during profiling.
    Related issue: https://github.com/Granulate/gprofiler/issues/640
    """
    profiler_flags.extend(["-d", "20"])
    container = start_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, output_directory, output_collapsed, runtime_specific_args, profiler_flags
    )
    try:
        wait_event(
            20,
            Event(),
            lambda: re.search(rb"Profiling process \d* with py-spy", container.logs()) is not None,
        )
    except TimeoutError:
        print(container.logs())
        raise
    time.sleep(2)
    application_container_name = application_docker_container.name
    application_docker_container.kill()
    collapsed_text = wait_for_gprofiler_container(container, output_collapsed)
    assert "py-spy> Stopped sampling because process exited" in container.logs().decode()
    assert f";{application_container_name};python" in collapsed_text


@pytest.mark.parametrize("in_container", [False])
@pytest.mark.parametrize("runtime", ["java"])
@pytest.mark.parametrize("profiler_type", ["ap"])
def test_profiling_provided_pids(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    runtime_specific_args: List[str],
    profiler_flags: List[str],
    application_factory: Callable[[], _GeneratorContextManager],
    profiler_type: str,
) -> None:
    """
    Tests that gprofiler will profile only processes provided via flag --pids
    """
    with application_factory() as pid1:
        with application_factory() as pid2:
            profiler_flags.extend(["--pids", str(pid1)])
            container = start_gprofiler_in_container_for_one_session(
                docker_client,
                gprofiler_docker_image,
                output_directory,
                output_collapsed,
                runtime_specific_args,
                profiler_flags,
            )
            wait_for_gprofiler_container(container, output_collapsed)
            assert "processes left after filtering: 1" in container.logs().decode()
            assert f"Profiling process {pid1} with async-profiler" in container.logs().decode()
            assert f"Profiling process {pid2} with async-profiler" not in container.logs().decode()
