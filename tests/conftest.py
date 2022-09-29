#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import stat
import subprocess
from contextlib import _GeneratorContextManager, contextmanager
from functools import lru_cache, partial
from pathlib import Path
from typing import Any, Callable, Dict, Generator, Iterable, Iterator, List, Mapping, Optional, Tuple, cast

import docker
import pytest
from _pytest.config import Config
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image
from psutil import Process
from pytest import FixtureRequest, TempPathFactory, fixture

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
def java_args() -> Tuple[str]:
    return cast(Tuple[str], ())


def make_path_world_accessible(path: Path) -> None:
    """
    Makes path and its subparts accessible by all.
    """
    chmod_path_parts(path, stat.S_IRGRP | stat.S_IROTH | stat.S_IXGRP | stat.S_IXOTH)


@fixture
def tmp_path_world_accessible(tmp_path: Path) -> Path:
    make_path_world_accessible(tmp_path)
    return tmp_path


@fixture(scope="session")
def artifacts_dir(tmp_path_factory: TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("artifacts")


@lru_cache(maxsize=None)
def java_command_line(path: Path, java_args: Tuple[str]) -> List[str]:
    class_path = path / "java"
    class_path.mkdir(exist_ok=True)
    # Java fails with permissions errors: "Error: Could not find or load main class Fibonacci"
    make_path_world_accessible(class_path)
    subprocess.run(["javac", CONTAINERS_DIRECTORY / "java/Fibonacci.java", "-d", class_path])
    return ["java"] + list(java_args) + ["-cp", str(class_path), "Fibonacci"]


@lru_cache(maxsize=None)
def dotnet_command_line(path: Path) -> List[str]:
    class_path = path / "dotnet" / "Fibonacci"
    class_path.mkdir(parents=True)
    make_path_world_accessible(class_path)
    subprocess.run(["cp", str(CONTAINERS_DIRECTORY / "dotnet/Fibonacci.cs"), class_path])
    subprocess.run(["dotnet", "new", "console", "--force"], cwd=class_path)
    subprocess.run(["rm", "Program.cs"], cwd=class_path)
    subprocess.run(
        [
            "dotnet",
            "publish",
            "-c",
            "Release",
            "-o",
            ".",
            "-p:UseRazorBuildServer=false",
            "-p:UseSharedCompilation=false",
        ],
        cwd=class_path,
    )
    return ["dotnet", str(class_path / "Fibonacci.dll"), "--project", str(class_path)]


@fixture
def command_line(runtime: str, artifacts_dir: Path, java_args: Tuple[str]) -> List[str]:
    if runtime.startswith("native"):
        # these do not have non-container application - so it will result in an error if the command
        # line is used.
        return ["/bin/false"]
    elif runtime == "java":
        return java_command_line(artifacts_dir, java_args)
    elif runtime == "python":
        # note: here we run "python /path/to/lister.py" while in the container test we have
        # "CMD /path/to/lister.py", to test processes with non-python /proc/pid/comm
        return ["python3", str(CONTAINERS_DIRECTORY / "python/lister.py")]
    elif runtime == "php":
        return ["php", str(CONTAINERS_DIRECTORY / "php/fibonacci.php")]
    elif runtime == "ruby":
        return ["ruby", str(CONTAINERS_DIRECTORY / "ruby/fibonacci.rb")]
    elif runtime == "dotnet":
        return dotnet_command_line(artifacts_dir)
    elif runtime == "nodejs":
        return [
            "node",
            "--perf-prof",
            "--interpreted-frames-native-stack",
            str(CONTAINERS_DIRECTORY / "nodejs/fibonacci.js"),
        ]
    else:
        raise NotImplementedError(runtime)


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


def _build_image(
    docker_client: DockerClient, runtime: str, dockerfile: str = "Dockerfile", **kwargs: Mapping[str, Any]
) -> Image:
    base_path = CONTAINERS_DIRECTORY / runtime
    return docker_client.images.build(path=str(base_path), rm=True, dockerfile=str(base_path / dockerfile), **kwargs)[0]


def image_name(runtime: str, image_tag: str) -> str:
    return runtime + ("_" + image_tag if image_tag else "")


@fixture(scope="session")
def application_docker_image_configs() -> Mapping[str, Dict[str, Any]]:
    runtime_image_listing: Dict[str, Dict[str, Dict[str, Any]]] = {
        "dotnet": {
            "": {},
        },
        "golang": {
            "": {},
        },
        "java": {
            "": {},
            "j9": dict(buildargs={"JAVA_BASE_IMAGE": "adoptopenjdk/openjdk8-openj9"}),
            "zing": dict(dockerfile="zing.Dockerfile"),
            "musl": dict(dockerfile="musl.Dockerfile"),
        },
        "native": {
            "fp": dict(dockerfile="fp.Dockerfile"),
            "dwarf": dict(dockerfile="dwarf.Dockerfile"),
            "change_comm": dict(dockerfile="change_comm.Dockerfile"),
            "thread_comm": dict(dockerfile="thread_comm.Dockerfile"),
        },
        "nodejs": {
            "": {},
        },
        "php": {
            "": {},
        },
        "python": {
            "": {},
            "libpython": dict(dockerfile="libpython.Dockerfile"),
            "2.7-glibc-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "2.7-slim"}),
            "2.7-musl-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "2.7-alpine"}),
            "3.5-glibc-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.5-slim"}),
            "3.5-musl-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.5-alpine"}),
            "3.6-glibc-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.6-slim"}),
            "3.6-musl-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.6-alpine"}),
            "3.7-glibc-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.7-slim"}),
            "3.7-musl-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.7-alpine"}),
            "3.8-glibc-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.8-slim"}),
            "3.8-musl-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.8-alpine"}),
            "3.9-glibc-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.9-slim"}),
            "3.9-musl-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.9-alpine"}),
            "3.10-glibc-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.10-slim"}),
            "3.10-musl-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.10-alpine"}),
            "2.7-glibc-uwsgi": dict(
                dockerfile="uwsgi.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "2.7"}
            ),  # not slim - need gcc
            "2.7-musl-uwsgi": dict(dockerfile="uwsgi.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "2.7-alpine"}),
            "3.7-glibc-uwsgi": dict(
                dockerfile="uwsgi.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.7"}
            ),  # not slim - need gcc
            "3.7-musl-uwsgi": dict(dockerfile="uwsgi.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.7-alpine"}),
        },
        "ruby": {"": {}},
    }

    images = {}
    for runtime, tags_listing in runtime_image_listing.items():
        for tag, kwargs in tags_listing.items():
            name = image_name(runtime, tag)
            assert name not in images

            assert runtime not in kwargs
            kwargs["runtime"] = runtime

            images[name] = kwargs
    return images


@fixture
def application_image_tag() -> str:
    # lets tests override this value and use a "tagged" image, e.g "musl" or "j9".
    return ""


@fixture
def application_docker_image(
    docker_client: DockerClient,
    application_docker_image_configs: Mapping[str, Dict[str, Any]],
    runtime: str,
    application_image_tag: str,
) -> Iterable[Image]:
    yield _build_image(docker_client, **application_docker_image_configs[image_name(runtime, application_image_tag)])


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
