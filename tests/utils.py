import os
import subprocess
from pathlib import Path
from threading import Event
from typing import Dict, List, Mapping, Optional, Tuple

from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from gprofiler.gprofiler_types import StackToSampleCount
from gprofiler.profilers.java import JAVA_ASYNC_PROFILER_DEFAULT_SAFEMODE, JAVA_SAFEMODE_ALL, JavaProfiler
from gprofiler.profilers.profiler_base import ProfilerInterface

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
    auto_remove=True,
    **extra_kwargs,
) -> Tuple[Optional[Container], str]:
    if volumes is None:
        volumes = {}
    container_or_logs = docker_client.containers.run(
        image,
        command,
        privileged=True,
        network_mode="host",
        pid_mode="host",
        userns_mode="host",
        volumes=volumes,
        auto_remove=auto_remove,
        stderr=True,
        **extra_kwargs,
    )
    if isinstance(container_or_logs, Container):
        container, logs = container_or_logs, container_or_logs.logs().decode()
    else:
        assert isinstance(container_or_logs, bytes), container_or_logs
        container, logs = None, container_or_logs.decode()

    # print, so failing tests display it
    print(
        "Container logs:",
        logs if len(logs) > 0 else "(empty, possibly because container was detached and is running now)",
    )
    return container, logs


def _no_errors(logs: str):
    # example line: [2021-06-12 10:13:57,528] ERROR: gprofiler: ruby profiling failed
    assert "] ERROR: " not in logs, "found ERRORs in gProfiler logs!"


def run_gprofiler_in_container(
    docker_client: DockerClient, image: Image, command: List[str], **kwargs
) -> Tuple[Optional[Container], str]:
    """
    Wrapper around run_privileged_container() that also verifies there are not ERRORs in gProfiler's output log.
    """
    assert "-v" in command, "plesae run with -v!"  # otherwise there are no loglevel prints
    container, logs = run_privileged_container(docker_client, image, command, **kwargs)
    if container is not None:
        _no_errors(container.logs().decode())
    else:
        _no_errors(logs)
    return container, logs


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


def assert_function_in_collapsed(function_name: str, collapsed: Mapping[str, int]) -> None:
    print(f"collapsed: {collapsed}")
    assert any(
        (function_name in record) for record in collapsed.keys()
    ), f"function {function_name!r} missing in collapsed data!"


def snapshot_one_collaped(profiler: ProfilerInterface) -> StackToSampleCount:
    result = profiler.snapshot()
    assert len(result) == 1
    return next(iter(result.values()))


def make_java_profiler(
    frequency: int = 11,
    duration: int = 1,
    storage_dir: str = None,
    stop_event: Event = Event(),
    java_async_profiler_buildids: bool = False,
    java_version_check: bool = True,
    java_async_profiler_mode: str = "cpu",
    java_async_profiler_safemode: int = JAVA_ASYNC_PROFILER_DEFAULT_SAFEMODE,
    java_async_profiler_args: str = "",
    java_safemode: str = JAVA_SAFEMODE_ALL,
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
        java_mode=java_mode,
    )
