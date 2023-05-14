#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import secrets
import stat
import subprocess
from contextlib import _GeneratorContextManager, contextmanager
from functools import lru_cache, partial
from pathlib import Path
from threading import Event
from typing import Any, Callable, Dict, Generator, Iterable, Iterator, List, Mapping, Optional, Tuple, cast

import docker
import pytest
from _pytest.config import Config
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image
from psutil import Process
from pytest import FixtureRequest, TempPathFactory, fixture

from gprofiler.consts import CPU_PROFILING_MODE
from gprofiler.containers_client import ContainerNamesClient
from gprofiler.diagnostics import set_diagnostics
from gprofiler.gprofiler_types import StackToSampleCount
from gprofiler.metadata.application_identifiers import (
    ApplicationIdentifiers,
    get_java_app_id,
    get_node_app_id,
    get_python_app_id,
    get_ruby_app_id,
)
from gprofiler.metadata.enrichment import EnrichmentOptions
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.java import AsyncProfiledProcess, JattachJcmdRunner
from gprofiler.state import init_state
from tests import CONTAINERS_DIRECTORY, PHPSPY_DURATION
from tests.utils import (
    _application_docker_container,
    _application_process,
    assert_function_in_collapsed,
    chmod_path_parts,
    find_application_pid,
    is_aarch64,
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
def insert_dso_name() -> bool:
    return False


@fixture
def java_args() -> Tuple[str]:
    return cast(Tuple[str], ())


@fixture()
def profiler_state(tmp_path: Path, insert_dso_name: bool) -> ProfilerState:
    return ProfilerState(
        stop_event=Event(),
        profile_spawned_processes=False,
        insert_dso_name=insert_dso_name,
        profiling_mode=CPU_PROFILING_MODE,
        container_names_client=ContainerNamesClient(),
        processes_to_profile=None,
        storage_dir=str(tmp_path),
    )


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
    assert precompiled is not None
    return Path(precompiled)


@fixture
def check_app_exited() -> bool:
    """Override this to prevent checking if app exited prematurely (useful for simulating crash)."""
    return True


@fixture
def application_process(
    in_container: bool, command_line: List[str], check_app_exited: bool, runtime: str
) -> Iterator[Optional[subprocess.Popen]]:
    if in_container:
        yield None
        return
    else:
        if is_aarch64():
            if runtime == "dotnet":
                pytest.xfail("This combination fails on aarch64, see https://github.com/Granulate/gprofiler/issues/755")
        with _application_process(command_line, check_app_exited) as popen:
            yield popen


@fixture(scope="session")
def docker_client() -> DockerClient:
    tests_id = secrets.token_hex(5)
    docker_client = docker.from_env()
    setattr(docker_client, "_gprofiler_test_id", tests_id)
    yield docker_client

    exited_ids: List[str] = []
    exited_container_list = docker_client.containers.list(filters={"status": "exited", "label": tests_id})
    for container in exited_container_list:
        exited_ids.append(container.id)

    pruned_ids = docker_client.containers.prune(filters={"label": tests_id}).get("ContainersDeleted", [])

    for exited_id in exited_ids.copy():
        if exited_id in pruned_ids:
            exited_ids.remove(exited_id)

    if len(exited_ids) > 0:
        raise Exception(f"Containers with ids {exited_ids} have not been properly pruned")


@fixture(scope="session")
def gprofiler_docker_image(docker_client: DockerClient) -> Iterable[Image]:
    # access the prebuilt image.
    # this is built in the CI, in the "Build gProfiler image" step.
    yield docker_client.images.get("gprofiler")


def build_image(
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
            "hotspot-jdk-8": {},  # add for clarity when testing with multiple JDKs
            "hotspot-jdk-11": dict(buildargs={"JAVA_BASE_IMAGE": "openjdk:11-jdk"}),
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
            "": dict(
                buildargs={
                    "NODE_RUNTIME_FLAGS": "--perf-prof --interpreted-frames-native-stack",
                    "NODE_IMAGE_TAG": "@sha256:59531d2835edd5161c8f9512f9e095b1836f7a1fcb0ab73e005ec46047384911",
                }
            ),
            "without-flags": dict(
                buildargs={
                    "NODE_RUNTIME_FLAGS": "",
                    "NODE_IMAGE_TAG": "@sha256:59531d2835edd5161c8f9512f9e095b1836f7a1fcb0ab73e005ec46047384911",
                }
            ),
            "10-glibc": dict(buildargs={"NODE_IMAGE_TAG": ":10-slim"}),
            "10-musl": dict(buildargs={"NODE_IMAGE_TAG": ":10.24.1-alpine"}),
            "11-glibc": dict(buildargs={"NODE_IMAGE_TAG": ":11-slim"}),
            "11-musl": dict(buildargs={"NODE_IMAGE_TAG": ":11-alpine"}),
            "12-glibc": dict(buildargs={"NODE_IMAGE": "centos/nodejs-12-centos7", "NODE_IMAGE_TAG": ":12"}),
            "12-musl": dict(buildargs={"NODE_IMAGE_TAG": ":12.22.12-alpine"}),
            "13-glibc": dict(buildargs={"NODE_IMAGE_TAG": ":13-slim"}),
            "13-musl": dict(buildargs={"NODE_IMAGE_TAG": ":13-alpine"}),
            "14-glibc": dict(buildargs={"NODE_IMAGE_TAG": ":14-slim"}),
            "14-musl": dict(buildargs={"NODE_IMAGE_TAG": ":14-alpine"}),
            "15-glibc": dict(buildargs={"NODE_IMAGE_TAG": ":15-slim"}),
            "15-musl": dict(buildargs={"NODE_IMAGE_TAG": ":15-alpine"}),
            "16-glibc": dict(buildargs={"NODE_IMAGE_TAG": ":16-slim"}),
            "16-musl": dict(buildargs={"NODE_IMAGE_TAG": ":16-alpine"}),
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
            "3.11-glibc-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.11-slim"}),
            "3.11-musl-python": dict(dockerfile="matrix.Dockerfile", buildargs={"PYTHON_IMAGE_TAG": "3.11-alpine"}),
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
    if is_aarch64():
        if runtime == "nodejs":
            if application_image_tag == "12-glibc":
                pytest.xfail("This test fails on aarch64, see https://github.com/Granulate/gprofiler/issues/758")
        if runtime == "java":
            if application_image_tag == "j9" or application_image_tag == "zing":
                pytest.xfail(
                    "Different JVMs are not supported on aarch64, see https://github.com/Granulate/gprofiler/issues/717"
                )
            if application_image_tag == "musl":
                pytest.xfail("This test does not work on aarch64 https://github.com/Granulate/gprofiler/issues/743")
    yield build_image(docker_client, **application_docker_image_configs[image_name(runtime, application_image_tag)])


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
    return find_application_pid(pid)


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
        "dotnet": ["--dotnet-mode=dotnet-trace"],  # enable .NET
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
        "nodejs": (get_node_app_id, "nodejs: /app/fibonacci.js (/app/fibonacci.js)"),
        "ruby": (get_ruby_app_id, "ruby: fibonacci.rb (/app/fibonacci.rb)"),
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
    flags = ["--no-java", "--no-python", "--no-php", "--no-ruby", "--no-nodejs", "--no-dotnet"]
    if f"--no-{runtime}" in flags:
        flags.remove(f"--no-{runtime}")
        flags.append(f"--{runtime}-mode={profiler_type}")
    return flags


@fixture(autouse=True, scope="session")
def _init_profiler() -> None:
    """
    Updates the global EnrichmentOptions for this process (for JavaProfiler, PythonProfiler etc that
    we run in this context)
    """
    ApplicationIdentifiers.init(
        EnrichmentOptions(
            profile_api_version=None,
            container_names=True,
            application_identifiers=True,
            application_identifier_args_filters=[],
            application_metadata=True,
        )
    )

    ApplicationIdentifiers.init_java(
        JattachJcmdRunner(stop_event=Event(), jattach_timeout=AsyncProfiledProcess._DEFAULT_JATTACH_TIMEOUT)
    )
    set_diagnostics(False)
    init_state(run_id="tests-run-id")


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
        output = subprocess.check_output("python3 --version", stderr=subprocess.STDOUT, shell=True)

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
