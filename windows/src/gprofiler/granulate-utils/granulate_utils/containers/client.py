#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import contextlib
from typing import List, Optional

from granulate_utils.containers.container import Container, ContainersClientInterface
from granulate_utils.containers.cri import CriClient
from granulate_utils.containers.docker import DockerClient
from granulate_utils.exceptions import ContainerNotFound, NoContainerRuntimesError


class ContainersClient(ContainersClientInterface):
    """
    Wraps DockerClient and CriClient to provide a unified view of all containers
    running on a system.
    Docker is going away in k8s (https://kubernetes.io/blog/2020/12/02/dont-panic-kubernetes-and-docker/)
    so this is our way to collect all containers in either case, whether Docker is used on this system
    or not.
    """

    def __init__(self) -> None:
        try:
            self._docker_client: Optional[DockerClient] = DockerClient()
        except Exception:
            self._docker_client = None

        try:
            self._cri_client: Optional[CriClient] = CriClient()
        except Exception:
            self._cri_client = None

        if self._docker_client is None and self._cri_client is None:
            raise NoContainerRuntimesError()

    def list_containers(self, all_info: bool = False) -> List[Container]:
        """
        Lists all containers running on this machine via DockerClient and CriClient.
        :param all_info: Collect more verbose information. Currently, this ensures that the pid field of each
                         Container object is filled in.
        """
        docker_containers = self._docker_client.list_containers(all_info) if self._docker_client is not None else []
        cri_containers = self._cri_client.list_containers(all_info) if self._cri_client is not None else []

        # start with all Docker containers
        containers = docker_containers.copy()
        # then add CRI containers that are not already listed
        # we collect containers from Docker first because Docker provides all information in one RPC go
        # (e.g, pid) so it's better to use when appropriate.
        # we need to collect from both, because Docker might be installed and running on systems where containerd
        # CRI is used, and containerd when asked over CRI won't list containers started by Docker (although in both
        # cases they are controlled by containerd); this happens due to the use of containerd namespaces,
        # Docker & CRI use different ones.
        for cri_container in cri_containers:
            matching_docker = filter(lambda c: c.id == cri_container.id, containers)
            try:
                docker_container = next(matching_docker)
                assert (
                    docker_container.name == cri_container.name
                ), f"Non matching names: {cri_container} {docker_container}"
            except StopIteration:
                containers.append(cri_container)

        return containers

    def get_container(self, container_id: str, all_info: bool) -> Container:
        with contextlib.suppress(ContainerNotFound):
            if self._docker_client is not None:
                return self._docker_client.get_container(container_id, all_info)

        with contextlib.suppress(ContainerNotFound):
            if self._cri_client is not None:
                return self._cri_client.get_container(container_id, all_info)

        raise ContainerNotFound(container_id)

    def get_runtimes(self) -> List[str]:
        return (self._docker_client.get_runtimes() if self._docker_client is not None else []) + (
            self._cri_client.get_runtimes() if self._cri_client is not None else []
        )
