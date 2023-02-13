from threading import Event
from typing import Optional

from gprofiler.containers_client import ContainerNamesClient
from gprofiler.utils import TemporaryDirectoryWithMode


class ProfilerState:
    # Class for storing generic state parameters. These parameters are the same for each profiler.
    # Thanks to that class adding new state parameters to profilers won't result changing code in every profiler.
    def __init__(
        self,
        stop_event: Event,
        storage_dir: str,
        profile_spawned_processes: bool,
        insert_dso_name: bool,
        profiling_mode: str,
        container_names_client: Optional[ContainerNamesClient],
    ) -> None:
        self._stop_event = stop_event
        self._profile_spawned_processes = profile_spawned_processes
        self._insert_dso_name = insert_dso_name
        self._profiling_mode = profiling_mode
        self._temporary_dir = TemporaryDirectoryWithMode(dir=storage_dir, mode=0o755)
        self._storage_dir = self._temporary_dir.name
        self._container_names_client = container_names_client
        if self._container_names_client:
            self._container_names_client.refresh_container_names_cache()

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

    def get_container_name(self, pid: int) -> str:
        if self._container_names_client:
            return self._container_names_client.get_container_name(pid)
        else:
            return ""
