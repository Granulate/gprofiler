#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from pathlib import Path
from threading import Event
from typing import Callable, List, Mapping, Optional

import pytest  # type: ignore
from docker import DockerClient
from docker.models.images import Image

from gprofiler.java import JavaProfiler
from gprofiler.merge import parse_one_collapsed
from gprofiler.php import PHPSpyProfiler
from gprofiler.python import PySpyProfiler, PythonEbpfProfiler
from tests import PHPSPY_DURATION
from tests.utils import (
    assert_function_in_collapsed,
    copy_file_from_image,
    copy_pyspy_from_image,
    resource_file_path,
    run_privileged_container,
)


@pytest.mark.parametrize("runtime", ["java"])
def test_java_from_host(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: Callable[[Optional[Mapping[str, int]]], None],
    gprofiler_docker_image: Image,
) -> None:
    # copy the entire "java" directory
    copy_file_from_image(
        gprofiler_docker_image,
        os.path.join("/app", "gprofiler", "resources", "java"),
        resource_file_path(""),
    )
    with JavaProfiler(1000, 1, True, Event(), str(tmp_path)) as profiler:
        process_collapsed = profiler.snapshot()
        assert_collapsed(process_collapsed.get(application_pid))


@pytest.mark.parametrize("runtime", ["python"])
def test_pyspy(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: Callable[[Optional[Mapping[str, int]]], None],
    gprofiler_docker_image: Image,
) -> None:
    copy_pyspy_from_image(gprofiler_docker_image)
    with PySpyProfiler(1000, 1, Event(), str(tmp_path)) as profiler:
        process_collapsed = profiler.snapshot()
        assert_collapsed(process_collapsed.get(application_pid))


@pytest.mark.parametrize("runtime", ["php"])
def test_phpspy(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: Callable[[Optional[Mapping[str, int]]], None],
    gprofiler_docker_image: Image,
) -> None:
    copy_file_from_image(
        gprofiler_docker_image,
        os.path.join("/app", "gprofiler", "resources", "php", "phpspy"),
        resource_file_path("php/phpspy"),
    )

    with PHPSpyProfiler(1000, PHPSPY_DURATION, Event(), str(tmp_path), php_process_filter="php") as profiler:
        process_collapsed = profiler.snapshot()
        assert_collapsed(process_collapsed.get(application_pid))


@pytest.mark.parametrize("runtime", ["python"])
def test_python_ebpf(
    tmp_path,
    application_pid,
    assert_collapsed,
    gprofiler_docker_image,
):
    # get PyPerf from the built container
    pyperf_path = resource_file_path(PythonEbpfProfiler.PYPERF_RESOURCE)
    copy_file_from_image(
        gprofiler_docker_image,
        os.path.join("/app", "gprofiler", "resources", PythonEbpfProfiler.PYPERF_RESOURCE),
        pyperf_path,
    )

    with PythonEbpfProfiler(1000, 5, Event(), str(tmp_path)) as profiler:
        process_collapsed = profiler.snapshot()
        collapsed = process_collapsed.get(application_pid)
        assert_collapsed(collapsed)
        assert_function_in_collapsed("sys_getdents64", collapsed)  # ensure kernels stacks exist


@pytest.mark.parametrize("runtime", ["java", "python", "php"])
def test_from_container(
    docker_client: DockerClient,
    application_pid: int,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    assert_collapsed: Callable[[Mapping[str, int]], None],
) -> None:
    _ = application_pid  # Fixture only used for running the application.
    inner_output_directory = "/tmp/gprofiler"
    volumes = {
        "/usr/src": {"bind": "/usr/src", "mode": "ro"},
        "/lib/modules": {"bind": "/lib/modules", "mode": "ro"},
        str(output_directory): {"bind": inner_output_directory, "mode": "rw"},
    }
    args = ["-d", "1", "-o", inner_output_directory] + runtime_specific_args
    run_privileged_container(docker_client, gprofiler_docker_image, args, volumes=volumes)

    collapsed = parse_one_collapsed(Path(output_directory / "last_profile.col").read_text())
    assert_collapsed(collapsed)
