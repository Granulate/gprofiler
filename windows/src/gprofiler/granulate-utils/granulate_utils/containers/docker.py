#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from typing import List, Optional

import docker
import docker.errors
import docker.models.containers

from granulate_utils.containers.container import Container, ContainersClientInterface
from granulate_utils.exceptions import ContainerNotFound
from granulate_utils.linux.ns import resolve_host_root_links

DOCKER_SOCK = "/var/run/docker.sock"


class DockerClient(ContainersClientInterface):
    def __init__(self) -> None:
        self._docker = docker.DockerClient(base_url="unix://" + resolve_host_root_links(DOCKER_SOCK))

    def list_containers(self, all_info: bool) -> List[Container]:
        containers = self._docker.containers.list()
        return list(map(self._create_container, containers))

    def get_container(self, container_id: str, all_info: bool) -> Container:
        try:
            container = self._docker.containers.get(container_id)
            return self._create_container(container)
        except docker.errors.NotFound:
            raise ContainerNotFound(container_id)

    def get_runtimes(self) -> List[str]:
        return ["docker"]

    @staticmethod
    def _create_container(container: docker.models.containers.Container) -> Container:
        pid: Optional[int] = container.attrs["State"].get("Pid")
        if pid == 0:  # Docker returns 0 for dead containers
            pid = None
        return Container(
            runtime="docker",
            name=container.name,
            id=container.id,
            labels=container.labels,
            running=container.status == "running",
            pid=pid,
        )
