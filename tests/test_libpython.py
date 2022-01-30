#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from pathlib import Path
from threading import Event
from typing import Callable, Mapping

import pytest
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from gprofiler.profilers.python import PythonProfiler
from tests import CONTAINERS_DIRECTORY
from tests.conftest import AssertInCollapsed
from tests.type_utils import cast_away_optional


@pytest.fixture
def runtime() -> str:
    return "python"


@pytest.fixture(scope="session")
def application_docker_image(docker_client: DockerClient) -> Image:
    dockerfile = CONTAINERS_DIRECTORY / "python" / "Dockerfile.libpython"
    image: Image = docker_client.images.build(path=str(dockerfile.parent), dockerfile=str(dockerfile))[0]
    yield image
    docker_client.images.remove(image.id, force=True)


@pytest.mark.parametrize("in_container", [True])
def test_python_select_by_libpython(
    tmp_path: Path,
    application_docker_container: Container,
    assert_collapsed: AssertInCollapsed,
) -> None:
    """
    Tests that profiling of processes running Python, whose basename(readlink("/proc/pid/exe")) isn't "python".
    (for example, uwsgi). We expect to select these because they have "libpython" in their "/proc/pid/maps".
    This test runs a Python named "shmython".
    """
    with PythonProfiler(1000, 1, Event(), str(tmp_path), "pyspy", True, None) as profiler:
        process_collapsed = profiler.snapshot()
    collapsed = cast_away_optional(process_collapsed.get(application_docker_container.attrs["State"]["Pid"]))
    assert_collapsed(collapsed)
    assert all(stack.startswith("shmython") for stack in collapsed.keys())
