#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import stat
import subprocess
from contextlib import _GeneratorContextManager, contextmanager
from functools import partial
from pathlib import Path
from typing import Any, Callable, Generator, Iterable, Iterator, List, Mapping, Optional, cast

import docker
import pytest
from _pytest.config import Config
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image
from psutil import Process
from pytest import FixtureRequest, fixture

from gprofiler.gprofiler_types import StackToSampleCount
from gprofiler.metadata.application_identifiers import get_java_app_id, get_python_app_id, set_enrichment_options
from gprofiler.metadata.enrichment import EnrichmentOptions
from tests import CONTAINERS_DIRECTORY, PARENT, PHPSPY_DURATION
from tests.utils import (
    _application_docker_container,
    _application_process,
    assert_function_in_collapsed,
    chmod_path_parts,
)


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
def chdir(path: Path) -> Iterator[None]:
    cwd = os.getcwd()
    try:
        os.chdir(path)
        yield
    finally:
        os.chdir(cwd)


@fixture(params=[False, True])
def in_container(request: FixtureRequest) -> bool:
    return cast(bool, request.param)  # type: ignore # SubRequest isn't exported yet,
    # https://github.com/pytest-dev/pytest/issues/7469


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
        "dotnet": ["dotnet", str(CONTAINERS_DIRECTORY / "dotnet/Fibonacci.dll")],
        "nodejs": [
            "node",
            "--perf-prof",
            "--interpreted-frames-native-stack",
            str(CONTAINERS_DIRECTORY / "nodejs/fibonacci.js"),
        ],
        # these do not have non-container application - so it will result in an error if the command
        # line is used.
        "native_fp": ["/bin/false"],
        "native_dwarf": ["/bin/false"],
    }[runtime]


@fixture
def application_executable(runtime: str) -> str:
    if runtime == "golang":
        return "fibonacci"
    elif runtime == "nodejs":
        return "node"
    return runtime


@fixture
def gprofiler_exe(request: FixtureRequest, tmp_path: Path) -> Path:
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


@fixture
def check_app_exited() -> bool:
    """Override this to prevent checking if app exited prematurely (useful for simulating crash)."""
    return True


@fixture
def application_process(
    in_container: bool, command_line: List[str], check_app_exited: bool
) -> Iterator[Optional[subprocess.Popen]]:
    if in_container:
        yield None
        return
    else:
        with _application_process(command_line, check_app_exited) as popen:
            yield popen


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
        if runtime == "native":
            path = CONTAINERS_DIRECTORY / runtime
            images[runtime + "_fp"], _ = docker_client.images.build(
                path=str(path), dockerfile=str(path / "fp.Dockerfile"), rm=True
            )
            images[runtime + "_dwarf"], _ = docker_client.images.build(
                path=str(path), dockerfile=str(path / "dwarf.Dockerfile"), rm=True
            )
            continue

        images[runtime], _ = docker_client.images.build(path=str(CONTAINERS_DIRECTORY / runtime), rm=True)

        # for java - add additional images
        if runtime == "java":
            images[runtime + "_j9"], _ = docker_client.images.build(
                path=str(CONTAINERS_DIRECTORY / runtime),
                rm=True,
                buildargs={"JAVA_BASE_IMAGE": "adoptopenjdk/openjdk8-openj9"},
            )

            images[runtime + "_zing"], _ = docker_client.images.build(
                path=str(CONTAINERS_DIRECTORY / runtime),
                rm=True,
                dockerfile=str(CONTAINERS_DIRECTORY / runtime / "zing.Dockerfile"),
            )

        # build musl image if exists
        musl_dockerfile = CONTAINERS_DIRECTORY / runtime / "musl.Dockerfile"
        if musl_dockerfile.exists():
            images[runtime + "_musl"], _ = docker_client.images.build(
                path=str(CONTAINERS_DIRECTORY / runtime), dockerfile=str(musl_dockerfile), rm=True
            )

    yield images
    for image in images.values():
        docker_client.images.remove(image.id, force=True)


@fixture
def image_suffix() -> str:
    # lets tests override this value and use a suffixed image, e.g _musl or _j9.
    return ""


@fixture
def application_docker_image(
    application_docker_images: Mapping[str, Image],
    runtime: str,
    image_suffix: str,
) -> Iterable[Image]:
    runtime = runtime + image_suffix
    yield application_docker_images[runtime]


@fixture
def application_docker_mount() -> bool:
    """
    Whether or not to mount the output directory (output_directory fixture) to the application containers.
    """
    return False


@fixture
def extra_application_docker_mounts() -> List[docker.types.Mount]:
    """
    Override to add additional docker mounts to the application container
    """
    return []


@fixture
def application_docker_mounts(
    application_docker_mount: bool,
    extra_application_docker_mounts: List[docker.types.Mount],
    output_directory: Path,
) -> List[docker.types.Mount]:
    mounts = []

    mounts.extend(extra_application_docker_mounts)

    if application_docker_mount:
        output_directory.mkdir(parents=True, exist_ok=True)
        mounts.append(
            docker.types.Mount(target=str(output_directory), type="bind", source=str(output_directory), read_only=False)
        )

    return mounts


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
    application_docker_mounts: List[docker.types.Mount],
    application_docker_capabilities: List[str],
) -> Iterable[Container]:
    if not in_container:
        yield None
        return
    else:
        with _application_docker_container(
            docker_client, application_docker_image, application_docker_mounts, application_docker_capabilities
        ) as container:
            yield container


@fixture
def output_directory(tmp_path: Path) -> Path:
    return tmp_path / "output"


@fixture
def output_collapsed(output_directory: Path) -> Path:
    return output_directory / "last_profile.col"


@fixture
def application_factory(
    in_container: bool,
    docker_client: DockerClient,
    application_docker_image: Image,
    output_directory: Path,
    application_docker_mounts: List[docker.types.Mount],
    application_docker_capabilities: List[str],
    command_line: List[str],
    check_app_exited: bool,
) -> Callable[[], _GeneratorContextManager]:
    @contextmanager
    def _run_application() -> Iterator[int]:
        if in_container:
            with _application_docker_container(
                docker_client, application_docker_image, application_docker_mounts, application_docker_capabilities
            ) as container:
                yield container.attrs["State"]["Pid"]
        else:
            with _application_process(command_line, check_app_exited) as process:
                yield process.pid

    return _run_application


@fixture
def application_pid(
    in_container: bool, application_process: subprocess.Popen, application_docker_container: Container
) -> int:
    pid: int = application_docker_container.attrs["State"]["Pid"] if in_container else application_process.pid

    # Application might be run using "sh -c ...", we detect the case and return the "real" application pid
    process = Process(pid)
    if process.cmdline()[0] == "sh" and process.cmdline()[1] == "-c" and len(process.children(recursive=False)) == 1:
        pid = process.children(recursive=False)[0].pid

    return pid


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


AssertInCollapsed = Callable[[StackToSampleCount], None]


@fixture
def assert_collapsed(runtime: str) -> AssertInCollapsed:
    function_name = {
        "java": "Fibonacci.main",
        "python": "burner",
        "php": "fibonacci",
        "ruby": "fibonacci",
        "nodejs": "fibonacci",
        "golang": "fibonacci",
        "dotnet": "Fibonacci",
    }[runtime]

    return partial(assert_function_in_collapsed, function_name)


@fixture
def assert_app_id(application_pid: int, runtime: str, in_container: bool) -> Generator:
    desired_name_and_getter = {
        "java": (get_java_app_id, "java: Fibonacci.jar"),
        "python": (get_python_app_id, "python: lister.py (/app/lister.py)"),
    }
    # We test the application name only after test has finished because the test may wait until the application is
    # running and application name might change.
    yield
    # TODO: Change commandline of processes running not in containers so we'll be able to match against them.
    if in_container and runtime in desired_name_and_getter:
        getter, name = desired_name_and_getter[runtime]
        # https://github.com/python/mypy/issues/10740
        assert getter(Process(application_pid)) == name  # type: ignore # noqa


@fixture
def exec_container_image(request: FixtureRequest, docker_client: DockerClient) -> Optional[Image]:
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
    if f"--no-{runtime}" in flags:
        flags.remove(f"--no-{runtime}")
        flags.append(f"--{runtime}-mode={profiler_type}")
    return flags


@fixture(autouse=True, scope="session")
def _set_enrichment_options() -> None:
    """
    Updates the global EnrichmentOptions for this process (for JavaProfiler, PythonProfiler etc that
    we run in this context)
    """
    set_enrichment_options(
        EnrichmentOptions(
            profile_api_version=None,
            container_names=True,
            application_identifiers=True,
            application_identifier_args_filters=[],
            application_metadata=True,
        )
    )


def pytest_addoption(parser: Any) -> None:
    parser.addoption("--exec-container-image", action="store", default=None)
    parser.addoption("--executable", action="store", default=None)


def pytest_collection_modifyitems(session: pytest.Session, config: Config, items: List[pytest.Item]) -> None:
    # run container tests before others.
    # when run in the CI, tests running the profiler on the host break, failing to execute local programs (e.g grep)
    # for whatever reason.
    # I assumed it has something to do with the bootstrap process of the runner, and indeed by running the container
    # tests first we were alleviated of those issues.
    items.sort(key=lambda i: not i.name.startswith("test_from_container"))


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
    return cast(str, output.decode().strip().split()[-1])


@fixture
def noexec_tmp_dir(in_container: bool, tmp_path: Path) -> Iterator[str]:
    if in_container:
        # only needed for non-container tests
        yield ""
        return

    tmpfs_path = tmp_path / "tmpfs"
    tmpfs_path.mkdir(0o755, exist_ok=True)
    try:
        subprocess.run(["mount", "-t", "tmpfs", "-o", "noexec", "none", str(tmpfs_path)], check=True)
        yield str(tmpfs_path)
    finally:
        subprocess.run(["umount", str(tmpfs_path)], check=True)
