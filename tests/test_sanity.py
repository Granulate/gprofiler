#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import pytest  # type: ignore
from glob import glob
from pathlib import Path
from threading import Event
from typing import Callable, Mapping

from docker import DockerClient
from docker.models.images import Image

from gprofiler.java import JavaProfiler
from gprofiler.python import PythonProfiler
from gprofiler.merge import parse_collapsed
from tests.util import run_privileged_container


@pytest.mark.parametrize("runtime", ["java"])
def test_java_from_host(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: Callable[[Mapping[str, int]], None],
) -> None:
    profiler = JavaProfiler(1000, 1, True, Event(), str(tmp_path))
    process_collapsed = profiler.profile_processes()
    assert_collapsed(process_collapsed.get(application_pid))


@pytest.mark.parametrize("runtime", ["python"])
def test_python_from_host(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: Callable[[Mapping[str, int]], None],
) -> None:
    profiler = PythonProfiler(1000, 1, Event(), str(tmp_path))
    process_collapsed = profiler.profile_processes()
    assert_collapsed(process_collapsed.get(application_pid))


@pytest.mark.parametrize("runtime", ["java", "python"])
def test_from_container(
    docker_client: DockerClient,
    application_pid: int,
    gprofiler_docker_image: Image,
    output_directory: Path,
    assert_collapsed: Callable[[Mapping[str, int]], None],
) -> None:
    _ = application_pid  # Fixture only used for running the application.
    inner_output_directory = "/tmp/gpofiler"
    volumes = {str(output_directory): {"bind": inner_output_directory, "mode": "rw"}}
    run_privileged_container(
        docker_client, gprofiler_docker_image, ["-d", "1", "-o", inner_output_directory], volumes=volumes
    )
    output = glob(str(output_directory / "*.col"))
    assert len(output) == 1
    collapsed_path = output[0]
    collapsed = parse_collapsed(Path(collapsed_path).read_text())
    assert_collapsed(collapsed)
