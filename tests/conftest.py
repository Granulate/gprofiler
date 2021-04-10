#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from pathlib import Path
from subprocess import Popen, run
from time import sleep
from typing import Callable, List, Mapping, Iterable

import docker
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image
from pytest import fixture  # type: ignore

from tests import PARENT, CONTAINERS_DIRECTORY


@fixture
def runtime():
    """
    Parametrize this with application runtime name (java, python).
    """
    raise NotImplementedError


@fixture(params=[False, True])
def in_container(request) -> bool:
    return request.param


def java_command_line(class_path: Path) -> List:
    class_path.mkdir()
    run(["javac", CONTAINERS_DIRECTORY / "java/Fibonacci.java", "-d", class_path])
    return ["java", "-cp", class_path, "Fibonacci"]


@fixture
def command_line(tmp_path: Path, runtime: str) -> List:
    return {
        "java": java_command_line(tmp_path / "java"),
        # note: here we run "python /path/to/fibonacci.py" while in the container test we have
        # "CMD /path/to/fibonacci.py", to test processes with non-python /proc/pid/comm
        "python": ["python3", CONTAINERS_DIRECTORY / "python/fibonacci.py"],
    }[runtime]


@fixture
def application_process(in_container: bool, command_line: List):
    if in_container:
        yield None
        return
    else:
        popen = Popen(command_line)
        yield popen
        popen.kill()


@fixture(scope="session")
def docker_client() -> DockerClient:
    return docker.from_env()


@fixture(scope="session")
def gprofiler_docker_image(docker_client: DockerClient) -> Iterable[Image]:
    image: Image = docker_client.images.build(path=str(PARENT))[0]
    yield image
    docker_client.images.remove(image.id, force=True)


@fixture(scope="session")
def application_docker_images(docker_client: DockerClient) -> Iterable[Mapping[str, Image]]:
    images = {}
    for runtime in os.listdir(str(CONTAINERS_DIRECTORY)):
        images[runtime], _ = docker_client.images.build(path=str(CONTAINERS_DIRECTORY / runtime))
    yield images
    for image in images.values():
        docker_client.images.remove(image.id, force=True)


@fixture
def application_docker_image(application_docker_images: Mapping[str, Image], runtime: str) -> Iterable[Image]:
    yield application_docker_images[runtime]


@fixture
def application_docker_container(
    in_container: bool, docker_client: DockerClient, application_docker_image: Image
) -> Iterable[Container]:
    if not in_container:
        yield None
        return
    else:
        container: Container = docker_client.containers.run(application_docker_image, detach=True, user="5555:6666")
        while container.status != "running":
            sleep(1)
            container.reload()
        yield container
        container.remove(force=True)


@fixture
def output_directory(tmp_path: Path) -> Path:
    return tmp_path / "output"


@fixture
def application_pid(in_container: bool, application_process: Popen, application_docker_container: Container):
    return application_docker_container.attrs["State"]["Pid"] if in_container else application_process.pid


@fixture
def assert_collapsed(runtime: str) -> Callable[[Mapping[str, int]], None]:
    function_name = {
        "java": "Fibonacci.main",
        "python": "fibonacci",
    }[runtime]

    def assert_collapsed(collapsed: Mapping[str, int]) -> None:
        assert collapsed is not None
        assert any((function_name in record) for record in collapsed.keys())

    return assert_collapsed
