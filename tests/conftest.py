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
from typing import Callable, Generator, Iterable, List, Mapping, Optional

import docker
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image
from pytest import fixture

from gprofiler.metadata.application_identifiers import get_application_name
from tests import CONTAINERS_DIRECTORY, PARENT, PHPSPY_DURATION
from tests.utils import assert_function_in_collapsed, chmod_path_parts


@fixture
def runtime() -> str:
    """
    Parametrize this with application runtime name (java, python, ..).
    """
    raise NotImplementedError


@fixture
def profiler_type() -> str:
    """
    Parametrize this with runtime profiler name (ap, py-spy, ...).
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


@fixture
def java_args() -> List[str]:
    return []


@fixture
def java_command_line(tmp_path: Path, java_args: List[str]) -> List[str]:
    class_path = tmp_path / "java"
    class_path.mkdir()
    # make all directories readable & executable by all.
    # Java fails with permissions errors: "Error: Could not find or load main class Fibonacci"
    chmod_path_parts(class_path, stat.S_IRGRP | stat.S_IROTH | stat.S_IXGRP | stat.S_IXOTH)
    subprocess.run(["javac", CONTAINERS_DIRECTORY / "java/Fibonacci.java", "-d", class_path])
    return ["java"] + java_args + ["-cp", str(class_path), "Fibonacci"]


@fixture
def command_line(runtime: str, java_command_line: List[str]) -> List[str]:
    return {
        "java": java_command_line,
        # note: here we run "python /path/to/lister.py" while in the container test we have
        # "CMD /path/to/lister.py", to test processes with non-python /proc/pid/comm
        "python": ["python3", str(CONTAINERS_DIRECTORY / "python/lister.py")],
        "php": ["php", str(CONTAINERS_DIRECTORY / "php/fibonacci.php")],
        "ruby": ["ruby", str(CONTAINERS_DIRECTORY / "ruby/fibonacci.rb")],
        "nodejs": [
            "node",
            "--perf-prof",
            "--interpreted-frames-native-stack",
            str(CONTAINERS_DIRECTORY / "nodejs/fibonacci.js"),
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
def check_app_exited() -> bool:
    """Override this to prevent checking if app exited prematurely (useful for simulating crash)."""
    return True


@fixture
def application_process(in_container: bool, command_line: List[str], check_app_exited: bool):
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

        if check_app_exited:
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
        musl_dockerfile = CONTAINERS_DIRECTORY / runtime / "musl.Dockerfile"
        if musl_dockerfile.exists():
            images[runtime + "_musl"], _ = docker_client.images.build(
                path=str(CONTAINERS_DIRECTORY / runtime), dockerfile=str(musl_dockerfile)
            )

    yield images
    for image in images.values():
        docker_client.images.remove(image.id, force=True)


@fixture
def musl() -> bool:
    # selects the musl version of an application image (e.g java:alpine)
    return False


@fixture
def application_docker_image(
    application_docker_images: Mapping[str, Image], runtime: str, musl: bool
) -> Iterable[Image]:
    runtime = runtime + ("_musl" if musl else "")
    yield application_docker_images[runtime]


@fixture
def application_docker_mount() -> bool:
    """
    Whether or not to mount the output directory (output_directory fixture) to the application containers.
    """
    return False


@fixture
def application_docker_capabilities() -> List[str]:
    """
    List of capabilities to add to the application containers.
    """
    return []


@fixture
def application_docker_container(
    in_container: bool,
    docker_client: DockerClient,
    application_docker_image: Image,
    output_directory: Path,
    application_docker_mount: bool,
    application_docker_capabilities: List[str],
) -> Iterable[Container]:
    if not in_container:
        yield None
        return
    else:
        volumes = (
            {str(output_directory): {"bind": str(output_directory), "mode": "rw"}} if application_docker_mount else {}
        )
        container: Container = docker_client.containers.run(
            application_docker_image,
            detach=True,
            user="5555:6666",
            volumes=volumes,
            cap_add=application_docker_capabilities,
        )
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
        "php": [
            # not enabled by default
            "--php-mode",
            "phpspy",
            "--php-proc-filter",
            "php",
            # phpspy needs a little more time to warm-up
            "-d",
            str(PHPSPY_DURATION),
        ],
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

    return partial(assert_function_in_collapsed, function_name)


@fixture
def assert_application_name(application_pid: int, runtime: str, in_container: bool) -> Generator:
    desired_names = {"java": "java: /app/Fibonacci.jar", "python": "python: /app/lister.py"}
    yield
    if in_container and runtime in desired_names:
        assert get_application_name(application_pid) == desired_names[runtime]


@fixture
def exec_container_image(request, docker_client: DockerClient) -> Optional[Image]:
    image_name = request.config.getoption("--exec-container-image")
    if image_name is None:
        return None

    return docker_client.images.pull(image_name)


@fixture
def no_kernel_headers() -> Iterable[None]:
    release = os.uname().release
    headers_dir = f"/lib/modules/{release}"
    tmp_headers = f"{headers_dir}_tmp_moved"
    assert not os.path.exists(tmp_headers), "leftovers! please clean it up"

    try:
        if os.path.exists(headers_dir):
            os.rename(headers_dir, tmp_headers)
        yield
    finally:
        if os.path.exists(tmp_headers):
            os.rename(tmp_headers, headers_dir)


@fixture
def profiler_flags(runtime: str, profiler_type: str) -> List[str]:
    # Execute only the tested profiler
    flags = ["--no-java", "--no-python", "--no-php", "--no-ruby", "--no-nodejs"]
    flags.remove(f"--no-{runtime}")
    flags.append(f"--{runtime}-mode={profiler_type}")
    return flags


def pytest_addoption(parser):
    parser.addoption("--exec-container-image", action="store", default=None)
    parser.addoption("--executable", action="store", default=None)


@fixture
def python_version(in_container: bool, application_docker_container: Container) -> Optional[str]:
    if in_container:
        exit_code, output = application_docker_container.exec_run(cmd="python --version")
        if exit_code != 0:
            return None
    else:
        # If not running in a container the test application runs on host
        output = subprocess.check_output("python --version", stderr=subprocess.STDOUT, shell=True)

    # Output is expected to look like e.g. "Python 3.9.7"
    return output.decode().strip().split()[-1]
