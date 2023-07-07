from threading import Event
from typing import List, Optional

from psutil import Process

from gprofiler.containers_client import ContainerNamesClient
from gprofiler.utils import TemporaryDirectoryWithMode


class ProfilerState:
    # Class for storing generic state parameters. These parameters are the same for each profiler.
    # Thanks to that class adding new state parameters to profilers won't result changing code in every profiler.
    def __init__(
        self,
        *,
        stop_event: Event,
        storage_dir: str,
        profile_spawned_processes: bool,
        insert_dso_name: bool,
        profiling_mode: str,
        container_names_client: Optional[ContainerNamesClient],
        processes_to_profile: Optional[List[Process]],
    ) -> None:
        self._stop_event = stop_event
        self._profile_spawned_processes = profile_spawned_processes
        self._insert_dso_name = insert_dso_name
        self._profiling_mode = profiling_mode
        self._temporary_dir = TemporaryDirectoryWithMode(dir=storage_dir, mode=0o755)
        self._storage_dir = self._temporary_dir.name
        self._container_names_client = container_names_client
        self._processes_to_profile = processes_to_profile

    @property
    def stop_event(self) -> Event:
        return self._stop_event

    @property
    def storage_dir(self) -> str:
        return str(self._storage_dir)

    @property
    def profile_spawned_processes(self) -> bool:
        return self._profile_spawned_processes

    @property
    def insert_dso_name(self) -> bool:
        return self._insert_dso_name

    @property
    def profiling_mode(self) -> str:
        return self._profiling_mode

    @property
    def container_names_client(self) -> Optional[ContainerNamesClient]:
        return self._container_names_client

    @property
    def processes_to_profile(self) -> Optional[List[Process]]:
        return self._processes_to_profile

    def get_container_name(self, pid: int) -> str:
        if self._container_names_client is not None:
            return self._container_names_client.get_container_name(pid)
        else:
            return ""
