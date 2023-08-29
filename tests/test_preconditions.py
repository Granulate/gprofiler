#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from contextlib import contextmanager
from pathlib import Path
from typing import List, Optional

import pytest
from docker import DockerClient
from docker.errors import ContainerError
from docker.models.containers import Container
from docker.models.images import Image

from tests.utils import start_gprofiler_in_container_for_one_session, wait_for_container, wait_for_log


def start_gprofiler(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    privileged: bool = True,
    user: int = 0,
    pid_mode: Optional[str] = "host",
    extra_profiler_args: Optional[List[str]] = None,
) -> Container:
    extra_profiler_args = extra_profiler_args or []
    return start_gprofiler_in_container_for_one_session(
        docker_client,
        gprofiler_docker_image,
        Path("/tmp"),
        Path("/tmp/collapsed"),
        [],
        ["-d", "1"] + extra_profiler_args,
        privileged=privileged,
        user=user,
        pid_mode=pid_mode,
    )


@contextmanager
def run_gprofiler_container(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    privileged: bool = True,
    user: int = 0,
    pid_mode: Optional[str] = "host",
    extra_profiler_args: Optional[List[str]] = None,
) -> Container:
    container: Container = None
    try:
        container = start_gprofiler(
            docker_client, gprofiler_docker_image, privileged, user, pid_mode, extra_profiler_args
        )
        yield container
    finally:
        if container is not None:
            container.stop()  # Fine to be run also if already stopped


def test_mutex_taken_twice(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
) -> None:
    """
    Mutex can only be taken once. Second gProfiler executed should fail with the mutex already taken error.
    """
    # Run the first one continuously
    with run_gprofiler_container(docker_client, gprofiler_docker_image, extra_profiler_args=["-c"]) as gprofiler1:
        wait_for_log(gprofiler1, "Running gProfiler", 0)
        with run_gprofiler_container(docker_client, gprofiler_docker_image) as gprofiler2:
            # exits without an error
            assert wait_for_container(gprofiler2) == (
                "Could not acquire gProfiler's lock. Is it already running?"
                " Try 'sudo netstat -xp | grep gprofiler' to see which process holds the lock.\n"
            )

    wait_for_container(gprofiler1)  # without an error as well


def test_not_root(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
) -> None:
    """
    gProfiler must run as root and should complain otherwise.
    """
    gprofiler = start_gprofiler(docker_client, gprofiler_docker_image, user=42)

    # exits without an error
    with pytest.raises(ContainerError) as e:
        wait_for_container(gprofiler)

    assert e.value.exit_status == 1
    assert e.value.stderr == b"Must run gprofiler as root, please re-run.\n"


def test_not_host_pid(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
) -> None:
    """
    gProfiler must run in host PID NS.
    """
    gprofiler = start_gprofiler(docker_client, gprofiler_docker_image, pid_mode=None)

    # exits without an error
    with pytest.raises(ContainerError) as e:
        wait_for_container(gprofiler)

    assert e.value.exit_status == 1
    assert e.value.stderr == (
        b"Please run me in the init PID namespace! In Docker, make sure you pass '--pid=host'."
        b" In Kubernetes, add 'hostPID: true' in the Pod spec.\n"
        b"You can disable this check with --disable-pidns-check.\n"
    )


def test_host_pid_not_privileged(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
) -> None:
    """
    When run in host PID NS but not privileged, we will fail to take the mutex.
    Ensure an appropriate message is written.
    """
    gprofiler = start_gprofiler(docker_client, gprofiler_docker_image, privileged=False, user=0, pid_mode="host")

    # exits without an error
    with pytest.raises(ContainerError) as e:
        wait_for_container(gprofiler)

    assert e.value.exit_status == 1
    assert e.value.stderr.endswith(
        b"Could not acquire gProfiler's lock due to an error. Are you running gProfiler in privileged mode?\n"
    )
