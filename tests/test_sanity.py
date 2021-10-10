#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from pathlib import Path
from threading import Event
from typing import Callable, List, Mapping

import pytest  # type: ignore
from docker import DockerClient
from docker.models.images import Image

from gprofiler.merge import parse_one_collapsed
from gprofiler.profilers.java import JavaProfiler
from gprofiler.profilers.perf import SystemProfiler
from gprofiler.profilers.php import PHPSpyProfiler
from gprofiler.profilers.python import PySpyProfiler, PythonEbpfProfiler
from gprofiler.profilers.ruby import RbSpyProfiler
from tests import PHPSPY_DURATION
from tests.utils import assert_function_in_collapsed, run_gprofiler_in_container


@pytest.mark.parametrize("runtime", ["java"])
def test_java_from_host(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed,
) -> None:
    with JavaProfiler(1000, 1, Event(), str(tmp_path), False, True, "itimer", "ap") as profiler:
        process_collapsed = profiler.snapshot().get(application_pid)
        assert_collapsed(process_collapsed, check_comm=True)


@pytest.mark.parametrize("runtime", ["python"])
def test_pyspy(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed,
    runtime: str,
) -> None:
    with PySpyProfiler(1000, 1, Event(), str(tmp_path)) as profiler:
        process_collapsed = profiler.snapshot().get(application_pid)
        assert_collapsed(process_collapsed, check_comm=True)


@pytest.mark.parametrize("runtime", ["php"])
def test_phpspy(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed,
    runtime: str,
) -> None:
    with PHPSpyProfiler(
        1000, PHPSPY_DURATION, Event(), str(tmp_path), php_process_filter="php", php_mode="phpspy"
    ) as profiler:
        process_collapsed = profiler.snapshot().get(application_pid)
        assert_collapsed(process_collapsed, check_comm=True)


@pytest.mark.parametrize("runtime", ["ruby"])
def test_rbspy(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed,
    gprofiler_docker_image: Image,
    runtime: str,
) -> None:
    with RbSpyProfiler(1000, 3, Event(), str(tmp_path), "rbspy") as profiler:
        process_collapsed = profiler.snapshot().get(application_pid)
        assert_collapsed(process_collapsed, check_comm=True)


@pytest.mark.parametrize("runtime", ["nodejs"])
def test_nodejs(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed,
    gprofiler_docker_image: Image,
    runtime: str,
) -> None:
    with SystemProfiler(
        1000, 6, Event(), str(tmp_path), perf_mode="fp", perf_inject=True, perf_dwarf_stack_size=0
    ) as profiler:
        process_collapsed = profiler.snapshot().get(application_pid)
        assert_collapsed(process_collapsed, check_comm=True)


@pytest.mark.parametrize("runtime", ["python"])
def test_python_ebpf(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed,
    gprofiler_docker_image: Image,
    runtime: str,
) -> None:
    with PythonEbpfProfiler(1000, 5, Event(), str(tmp_path)) as profiler:
        collapsed = profiler.snapshot()
        process_collapsed = collapsed.get(application_pid)
        assert_collapsed(process_collapsed, check_comm=True)
        assert_function_in_collapsed(
            "do_syscall_64_[k]", "python", process_collapsed, True
        )  # ensure kernels stacks exist
        assert_function_in_collapsed(
            "_PyEval_EvalFrameDefault_[pn]", "python", process_collapsed, True
        )  # ensure native user stacks exist


@pytest.mark.parametrize("runtime", ["java", "python", "php", "ruby"])
def test_from_container(
    docker_client: DockerClient,
    application_pid: int,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    assert_collapsed: Callable[[Mapping[str, int]], None],
    runtime: str,
) -> None:
    _ = application_pid  # Fixture only used for running the application.
    inner_output_directory = "/tmp/gprofiler"
    volumes = {
        "/usr/src": {"bind": "/usr/src", "mode": "ro"},
        "/lib/modules": {"bind": "/lib/modules", "mode": "ro"},
        str(output_directory): {"bind": inner_output_directory, "mode": "rw"},
    }

    # Execute only the tested profiler
    flags = ["--no-java", "--no-python", "--no-php", "--no-ruby"]
    flags.remove(f"--no-{runtime}")

    args = ["-v", "-d", "3", "-o", inner_output_directory] + runtime_specific_args + flags
    run_gprofiler_in_container(docker_client, gprofiler_docker_image, args, volumes=volumes)

    collapsed = parse_one_collapsed(Path(output_directory / "last_profile.col").read_text())
    assert_collapsed(collapsed)
