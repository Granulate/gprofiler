#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from docker import DockerClient
from docker.models.images import Image
import pytest  # type: ignore

from gprofiler.python import PythonProfiler

from tests.conftest import CONTAINERS_DIRECTORY  # type: ignore


@pytest.fixture(scope="session")
def application_docker_image(docker_client: DockerClient, runtime: str) -> Image:
    dockerfile = CONTAINERS_DIRECTORY / runtime / "Dockerfile.libpython"
    image: Image = docker_client.images.build(path=str(dockerfile.parent), dockerfile=str(dockerfile))[0]
    yield image
    docker_client.images.remove(image.id, force=True)


@pytest.fixture(scope="session")
def runtime(request) -> str:
    return "python"


def test_python_select_by_libpython(
    profiler: PythonProfiler,
    application_docker_container,
    output_directory,
    assert_collapsed,
) -> None:
    """
    Tests that profiling of processes running Python, whose basename(readlink("/proc/pid/exe")) isn't "python".
    (for example, uwsgi). We expect to select these because they have "libpython" in their "/proc/pid/maps".
    """
    process_collapsed = profiler.profile_processes()
    assert_collapsed(process_collapsed.get(application_docker_container.attrs["State"]["Pid"]))
