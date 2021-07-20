#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import stat
import subprocess
from contextlib import contextmanager
from functools import partial
from pathlib import Path
from time import sleep
from typing import Callable, Iterable, List, Mapping, Optional

import docker
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image
from pytest import fixture  # type: ignore

from tests import CONTAINERS_DIRECTORY, PARENT, PHPSPY_DURATION
from tests.utils import assert_function_in_collapsed, chmod_path_parts


@fixture
def runtime():
    """
    Parametrize this with application runtime name (java, python).
    """
    raise NotImplementedError


@contextmanager
def chdir(path):
    cwd = os.getcwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(cwd)


@fixture(params=[False, True])
def in_container(request) -> bool:
    return request.param


def java_command_line(class_path: Path) -> List:
    class_path.mkdir()
    # make all directories readable & executable by all.
    # Java fails with permissions errors: "Error: Could not find or load main class Fibonacci"
    chmod_path_parts(class_path, stat.S_IRGRP | stat.S_IROTH | stat.S_IXGRP | stat.S_IXOTH)
    subprocess.run(["javac", CONTAINERS_DIRECTORY / "java/Fibonacci.java", "-d", class_path])
    return ["java", "-cp", class_path, "Fibonacci"]


@fixture
def command_line(tmp_path: Path, runtime: str) -> List:
    return {
        "java": java_command_line(tmp_path / "java"),
        # note: here we run "python /path/to/lister.py" while in the container test we have
        # "CMD /path/to/lister.py", to test processes with non-python /proc/pid/comm
        "python": ["python3", CONTAINERS_DIRECTORY / "python/lister.py"],
        "php": ["php", CONTAINERS_DIRECTORY / "php/fibonacci.php"],
        "ruby": ["ruby", CONTAINERS_DIRECTORY / "ruby/fibonacci.rb"],
        "nodejs": [
            "node",
            "--perf-prof",
            "--interpreted-frames-native-stack",
            CONTAINERS_DIRECTORY / "nodejs/fibonacci.js",
        ],
    }[runtime]


@fixture
def gprofiler_exe(request, tmp_path: Path) -> Path:
    precompiled = request.config.getoption("--executable")
    if precompiled is not None:
        return Path(precompiled)

    with chdir(PARENT):
        pyi_popen = subprocess.Popen(
            ["pyinstaller", "--distpath", str(tmp_path), "pyinstaller.spec"],
        )
        pyi_popen.wait()

    staticx_popen = subprocess.Popen(["staticx", tmp_path / "gprofiler", tmp_path / "gprofiler"])
    staticx_popen.wait()
    return tmp_path / "gprofiler"


def _print_process_output(popen: subprocess.Popen) -> None:
    stdout, stderr = popen.communicate()
    print(f"stdout: {stdout.decode()}")
    print(f"stderr: {stderr.decode()}")


@fixture
def application_process(in_container: bool, command_line: List):
    if in_container:
        yield None
        return
    else:
        # run as non-root to catch permission errors, etc.
        def lower_privs():
            os.setgid(1000)
            os.setuid(1000)

        popen = subprocess.Popen(
            command_line, preexec_fn=lower_privs, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/tmp"
        )
        try:
            # wait 2 seconds to ensure it starts
            popen.wait(2)
        except subprocess.TimeoutExpired:
            pass
        else:
            _print_process_output(popen)
            raise Exception(f"Command {command_line} exited unexpectedly with {popen.returncode}")

        yield popen

        # ensure, again, that it still alive (if it exited prematurely it might provide bad data for the tests)
        try:
            popen.wait(0)
        except subprocess.TimeoutExpired:
            pass
        else:
            _print_process_output(popen)
            raise Exception(f"Command {command_line} exited unexpectedly during the test with {popen.returncode}")

        popen.kill()
        _print_process_output(popen)


@fixture(scope="session")
def docker_client() -> DockerClient:
    return docker.from_env()


@fixture(scope="session")
def gprofiler_docker_image(docker_client: DockerClient) -> Iterable[Image]:
    # access the prebuilt image.
    # this is built in the CI, in the "Build gProfiler image" step.
    yield docker_client.images.get("gprofiler")


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
            if container.status == "exited":
                raise Exception(container.logs().decode())
            sleep(1)
            container.reload()
        yield container
        container.remove(force=True)


@fixture
def output_directory(tmp_path: Path) -> Path:
    return tmp_path / "output"


@fixture
def application_pid(in_container: bool, application_process: subprocess.Popen, application_docker_container: Container):
    return application_docker_container.attrs["State"]["Pid"] if in_container else application_process.pid


@fixture
def runtime_specific_args(runtime: str) -> List[str]:
    return {
        "php": ["--php-proc-filter", "php", "-d", str(PHPSPY_DURATION)],  # phpspy needs a little more time to warm-up
        "python": ["-d", "3"],  # Burner python tests make syscalls and we want to record python + kernel stacks
        "nodejs": ["--nodejs-mode", "perf"],  # enable NodeJS profiling
    }.get(runtime, [])


@fixture
def assert_collapsed(runtime: str) -> Callable[[Mapping[str, int], bool], None]:
    function_name = {
        "java": "Fibonacci.main",
        "python": "burner",
        "php": "fibonacci",
        "ruby": "fibonacci",
        "nodejs": "fibonacci",
    }[runtime]

    return partial(assert_function_in_collapsed, function_name, runtime)


@fixture
def exec_container_image(request, docker_client: DockerClient) -> Optional[Image]:
    image_name = request.config.getoption("--exec-container-image")
    if image_name is None:
        return None

    return docker_client.images.pull(image_name)


def pytest_addoption(parser):
    parser.addoption("--exec-container-image", action="store", default=None)
    parser.addoption("--executable", action="store", default=None)
