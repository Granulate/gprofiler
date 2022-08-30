#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from pathlib import Path

from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from tests.utils import start_gprofiler_in_container_for_one_session, wait_for_container


def start_gprofiler(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
) -> Container:
    return start_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, Path("/tmp"), Path("/tmp/collapsed"), [], ["-d", "1"]
    )


def test_mutex(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
) -> None:
    gprofiler1 = start_gprofiler(docker_client, gprofiler_docker_image)
    gprofiler2 = start_gprofiler(docker_client, gprofiler_docker_image)

    # exits without an error
    assert wait_for_container(gprofiler2) == (
        "Could not acquire gProfiler's lock. Is it already running?"
        " Try 'sudo netstat -xp | grep gprofiler' to see which process holds the lock.\n"
    )

    wait_for_container(gprofiler1)  # without an error as well
