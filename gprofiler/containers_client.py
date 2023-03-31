#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from typing import Dict, List, Optional, Set

from granulate_utils.containers.client import ContainersClient
from granulate_utils.exceptions import NoContainerRuntimesError
from granulate_utils.linux.containers import get_process_container_id
from psutil import NoSuchProcess, Process

from gprofiler.log import get_logger_adapter
from gprofiler.utils.perf import valid_perf_pid

logger = get_logger_adapter(__name__)


class ContainerNamesClient:
    def __init__(self) -> None:
        try:
            self._containers_client: Optional[ContainersClient] = ContainersClient()
            logger.info(f"Discovered container runtimes: {self._containers_client.get_runtimes()}")
        except NoContainerRuntimesError:
            logger.warning(
                "Could not find a Docker daemon or CRI-compatible daemon, profiling data will not"
                " include the container names. If you do have a containers runtime and it's not supported,"
                " please open a new issue here:"
                " https://github.com/Granulate/gprofiler/issues/new"
            )
            self._containers_client = None

        self._pid_to_container_name_cache: Dict[int, str] = {}
        self._current_container_names: Set[str] = set()
        self._container_id_to_name_cache: Dict[str, Optional[str]] = {}

    def reset_cache(self) -> None:
        self._pid_to_container_name_cache.clear()
        self._current_container_names.clear()

    @property
    def container_names(self) -> List[str]:
        return list(self._current_container_names)

    def get_container_name(self, pid: int) -> str:
        if self._containers_client is None:
            return ""

        if not valid_perf_pid(pid):
            return ""

        if pid in self._pid_to_container_name_cache:
            return self._pid_to_container_name_cache[pid]

        container_name: Optional[str] = self._safely_get_process_container_name(pid)
        if container_name is None:
            self._pid_to_container_name_cache[pid] = ""
            return ""

        self._pid_to_container_name_cache[pid] = container_name
        return container_name

    def _safely_get_process_container_name(self, pid: int) -> Optional[str]:
        try:
            try:
                container_id = get_process_container_id(Process(pid))
                if container_id is None:
                    return None
            except NoSuchProcess:
                return None
            return self._get_container_name(container_id)
        except Exception:
            logger.warning(f"Could not get a container name for PID {pid}", exc_info=True)
            return None

    def _get_container_name(self, container_id: str) -> Optional[str]:
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

    def _refresh_container_names_cache(self) -> None:
        # We re-fetch all of the currently running containers, so in order to keep the cache small we clear it
        self._container_id_to_name_cache.clear()
        for container in self._containers_client.list_containers() if self._containers_client is not None else []:
            self._container_id_to_name_cache[container.id] = container.name
