#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import re
from typing import Dict, List, Optional, Set

import docker

from gprofiler.log import get_logger_adapter

CONTAINER_ID_PATTERN = re.compile(r"[a-f0-9]{64}")

logger = get_logger_adapter(__name__)


class DockerClient:
    def __init__(self):
        try:
            self._client = docker.from_env()
        except Exception:
            logger.warning(
                'Could not initiate the Docker client, so the profiling data will not include the container'
                ' names. If you are running gProfiler in a container, please mount the Docker sock file'
                ' by running the Docker run command with the following argument:'
                ' "-v /var/run/docker.sock:/var/run/docker.sock". Otherwise, please open a new issue here:'
                ' https://github.com/Granulate/gprofiler/issues/new'
            )
            self._client = None

        self._pid_to_container_name_cache: Dict[int, str] = {}
        self._current_container_names: Set[str] = set()
        self._container_id_to_name_cache: Dict[str, Optional[str]] = {}

    def reset_cache(self):
        self._pid_to_container_name_cache.clear()
        self._current_container_names.clear()

    @property
    def container_names(self) -> List[str]:
        return list(self._current_container_names)

    def get_container_name(self, pid: int) -> str:
        if self._client is None:
            return ''
        if pid in self._pid_to_container_name_cache:
            return self._pid_to_container_name_cache[pid]
        container_name: Optional[str] = self._safely_get_process_container_name(pid)
        if container_name is None:
            self._pid_to_container_name_cache[pid] = ''
            return ''
        self._pid_to_container_name_cache[pid] = container_name
        return container_name

    def _safely_get_process_container_name(self, pid: int) -> Optional[str]:
        try:
            container_id = self._get_process_container_id(pid)
            if container_id is None:
                return None
            return self._get_container_name(container_id)
        except Exception:
            logger.warning(f'Could not get a container name for PID {pid}', exc_info=True)
            return None

    def _get_container_name(self, container_id) -> Optional[str]:
        if container_id in self._container_id_to_name_cache:
            container_name = self._container_id_to_name_cache[container_id]
            if container_name is not None:
                # Might happen a few times for the same container name, so we use a set to have unique values
                self._current_container_names.add(container_name)
            return container_name

        self._refresh_container_names_cache()
        if container_id not in self._container_id_to_name_cache:
            self._container_id_to_name_cache[container_id] = None
            return None
        container_name = self._container_id_to_name_cache[container_id]
        if container_name is not None:
            self._current_container_names.add(container_name)
        return container_name

    def _refresh_container_names_cache(self):
        # We re-fetch all of the currently running containers, so in order to keep the cache small we clear it
        self._container_id_to_name_cache.clear()
        running_containers = self._client.containers.list()
        for container in running_containers:
            self._container_id_to_name_cache[container.id] = container.name

    @staticmethod
    def _get_process_container_id(pid: int) -> Optional[str]:
        # ECS uses /ecs/uuid/container-id
        # standard Docker uses /docker/container-id
        # k8s uses /kubepods/{burstable,besteffort}/uuid/container-id
        try:
            with open(f"/proc/{pid}/cgroup", 'r') as cgroup_file:
                cgroup = cgroup_file.read()
        except FileNotFoundError:
            # The process died before we got to this point
            return None

        for line in cgroup.split():
            found = CONTAINER_ID_PATTERN.findall(line)
            if found:
                return found[-1]

        return None
