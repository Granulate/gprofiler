#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from pathlib import Path
from subprocess import Popen, run
from threading import Event
from time import sleep
from typing import Callable, List, Union, Dict, Generator, Mapping

import docker
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image
from pytest import fixture  # type: ignore

from gprofiler.java import JavaProfiler
from gprofiler.python import PythonProfiler

HERE = Path(__file__).parent
PARENT = HERE.parent
CONTAINERS_DIRECTORY = HERE / "containers"


@fixture(scope="session", params=["java", "python"])
def runtime(request) -> str:
    return request.param


@fixture(scope="session", params=[False, True])
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
def application_process(command_line: List) -> Generator[Popen, None, None]:
    popen = Popen(command_line)
    yield popen
    popen.kill()


@fixture(scope="session")
def docker_client() -> DockerClient:
    return docker.from_env()


@fixture(scope="session")
def gprofiler_docker_image(docker_client: DockerClient) -> Image:
    image: Image = docker_client.images.build(path=str(PARENT))[0]
    yield image
    docker_client.images.remove(image.id, force=True)


@fixture(scope="session")
def application_docker_image(docker_client: DockerClient, runtime: str) -> Image:
    image: Image = docker_client.images.build(path=str(CONTAINERS_DIRECTORY / runtime))[0]
    yield image
    docker_client.images.remove(image.id, force=True)


@fixture
def application_docker_container(docker_client: DockerClient, application_docker_image: Image) -> Container:
    container: Container = docker_client.containers.run(application_docker_image, detach=True, user="5555:6666")
    while container.status != "running":
        sleep(1)
        container.reload()
    yield container
    container.remove(force=True)


@fixture
def output_directory(tmp_path: Path) -> Path:
    return tmp_path / "output"


# TODO: Avoid running the not chosen application variant.
@fixture
def application_pid(in_container: bool, application_process: Popen, application_docker_container: Container):
    return application_docker_container.attrs["State"]["Pid"] if in_container else application_process.pid


@fixture
def profiler(tmp_path: Path, runtime: str) -> Union[JavaProfiler, PythonProfiler]:
    profilers: Dict[str, Union[JavaProfiler, PythonProfiler]] = {
        "java": JavaProfiler(1000, 1, True, Event(), str(tmp_path)),
        "python": PythonProfiler(1000, 1, Event(), str(tmp_path)),
    }
    return profilers[runtime]


@fixture(scope="session")
def assert_collapsed(runtime: str) -> Callable[[Mapping[str, int]], None]:
    function_name = {
        "java": "Fibonacci.main",
        "python": "fibonacci",
    }[runtime]

    def assert_collapsed(collapsed: Mapping[str, int]) -> None:
        assert collapsed is not None
        assert any((function_name in record) for record in collapsed.keys())

    return assert_collapsed
