import os
import subprocess
from pathlib import Path
from threading import Event
from typing import Any, Dict, List

from docker import DockerClient
from docker.errors import ContainerError
from docker.models.images import Image

from gprofiler.gprofiler_types import ProfileData, StackToSampleCount
from gprofiler.profilers.java import (
    JAVA_ASYNC_PROFILER_DEFAULT_SAFEMODE,
    JAVA_SAFEMODE_ALL,
    AsyncProfiledProcess,
    JavaProfiler,
)
from gprofiler.profilers.profiler_base import ProfilerInterface
from gprofiler.utils import remove_path

RUNTIME_PROFILERS = [
    ("java", "ap"),
    ("python", "py-spy"),
    ("python", "pyperf"),
    ("php", "phpspy"),
    ("ruby", "rbspy"),
    ("nodejs", "perf"),
]


def run_privileged_container(
    docker_client: DockerClient,
    image: Image,
    command: List[str],
    volumes: Dict[str, Dict[str, str]] = None,
    **extra_kwargs: Any,
) -> str:
    if volumes is None:
        volumes = {}

    container = None
    try:
        container = docker_client.containers.run(
            image,
            command,
            privileged=True,
            network_mode="host",
            pid_mode="host",
            userns_mode="host",
            volumes=volumes,
            stderr=True,
            detach=True,
            **extra_kwargs,
        )
        # let it finish
        exit_status = container.wait()["StatusCode"]
        # and read its log
        logs = container.logs(stdout=True, stderr=True)

        if exit_status != 0:
            raise ContainerError(container, exit_status, command, image, logs)
    finally:
        if container is not None:
            container.remove()

    # print, so failing tests display it
    print(
        "Container logs:",
        logs if len(logs) > 0 else "(empty, possibly because container was detached and is running now)",
    )

    assert isinstance(logs, bytes), logs
    return logs.decode()


def _no_errors(logs: str) -> None:
    # example line: [2021-06-12 10:13:57,528] ERROR: gprofiler: ruby profiling failed
    assert "] ERROR: " not in logs, f"found ERRORs in gProfiler logs!: {logs}"


def run_gprofiler_in_container(docker_client: DockerClient, image: Image, command: List[str], **kwargs: Any) -> None:
    """
    Wrapper around run_privileged_container() that also verifies there are not ERRORs in gProfiler's output log.
    """
    assert "-v" in command, "plesae run with -v!"  # otherwise there are no loglevel prints
    logs = run_privileged_container(docker_client, image, command, **kwargs)
    _no_errors(logs)


def copy_file_from_image(image: Image, container_path: str, host_path: str) -> None:
    os.makedirs(os.path.dirname(host_path), exist_ok=True)
    # I tried writing it with the docker-py API, but retrieving large files with container.get_archive() just hangs...
    subprocess.run(
        f"c=$(docker container create {image.id}) && "
        f"{{ docker cp $c:{container_path} {host_path}; ret=$?; docker rm $c > /dev/null; exit $ret; }}",
        shell=True,
        check=True,
    )


def chmod_path_parts(path: Path, add_mode: int) -> None:
    """
    Adds 'add_mode' to all parts in 'path'.
    """
    for i in range(1, len(path.parts)):
        subpath = os.path.join(*path.parts[:i])
        os.chmod(subpath, os.stat(subpath).st_mode | add_mode)


def assert_function_in_collapsed(function_name: str, collapsed: StackToSampleCount) -> None:
    print(f"collapsed: {collapsed}")
    assert any(
        (function_name in record) for record in collapsed.keys()
    ), f"function {function_name!r} missing in collapsed data!"


def snapshot_one_profile(profiler: ProfilerInterface) -> ProfileData:
    result = profiler.snapshot()
    assert len(result) == 1
    return next(iter(result.values()))


def snapshot_one_collapsed(profiler: ProfilerInterface) -> StackToSampleCount:
    return snapshot_one_profile(profiler).stacks


def snapshot_pid_collapsed(profiler: ProfilerInterface, pid: int) -> StackToSampleCount:
    return profiler.snapshot()[pid].stacks


def make_java_profiler(
    frequency: int = 11,
    duration: int = 1,
    stop_event: Event = Event(),
    storage_dir: str = None,
    java_async_profiler_buildids: bool = False,
    java_version_check: bool = True,
    java_async_profiler_mode: str = "cpu",
    java_async_profiler_safemode: int = JAVA_ASYNC_PROFILER_DEFAULT_SAFEMODE,
    java_async_profiler_args: str = "",
    java_safemode: str = JAVA_SAFEMODE_ALL,
    java_jattach_timeout: int = AsyncProfiledProcess._JATTACH_TIMEOUT,
    java_async_profiler_mcache: int = AsyncProfiledProcess._DEFAULT_MCACHE,
    java_mode: str = "ap",
) -> JavaProfiler:
    assert storage_dir is not None
    return JavaProfiler(
        frequency=frequency,
        duration=duration,
        stop_event=stop_event,
        storage_dir=storage_dir,
        java_async_profiler_buildids=java_async_profiler_buildids,
        java_version_check=java_version_check,
        java_async_profiler_mode=java_async_profiler_mode,
        java_async_profiler_safemode=java_async_profiler_safemode,
        java_async_profiler_args=java_async_profiler_args,
        java_safemode=java_safemode,
        java_jattach_timeout=java_jattach_timeout,
        java_async_profiler_mcache=java_async_profiler_mcache,
        java_mode=java_mode,
    )


def run_gprofiler_in_container_for_one_session(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    output_directory: Path,
    runtime_specific_args: List[str],
    profiler_flags: List[str],
) -> str:
    """
    Runs the gProfiler container image for a single profiling session, and collects the output.
    """
    inner_output_directory = "/tmp/gprofiler"
    volumes = {
        str(output_directory): {"bind": inner_output_directory, "mode": "rw"},
    }
    args = ["-v", "-d", "3", "-o", inner_output_directory] + runtime_specific_args + profiler_flags

    output_path = Path(output_directory / "last_profile.col")
    remove_path(str(output_path), missing_ok=True)

    run_gprofiler_in_container(docker_client, gprofiler_docker_image, args, volumes=volumes)

    return output_path.read_text()
