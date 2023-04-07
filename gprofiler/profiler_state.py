from threading import Event
from typing import Any, List, Optional

from gprofiler.containers_client import ContainerNamesClient
from gprofiler.utils import TemporaryDirectoryWithMode


class ProfilerState:
    # Class for storing generic state parameters. These parameters are the same for each profiler.
    # Thanks to that class adding new state parameters to profilers won't result changing code in every profiler.
    def __init__(self, **kwargs: Any) -> None:
        self._stop_event: Event = kwargs.pop("stop_event")
        self._profile_spawned_processes: bool = kwargs.pop("profile_spawned_processes")
        self._insert_dso_name: bool = kwargs.pop("insert_dso_name")
        self._profiling_mode: str = kwargs.pop("profiling_mode")
        self._temporary_dir = TemporaryDirectoryWithMode(dir=kwargs.pop("storage_dir"), mode=0o755)
        self._storage_dir = self._temporary_dir.name
        self._container_names_client: Optional[ContainerNamesClient] = kwargs.get("container_names_client", None)
        self._pids_to_profile: Optional[List[int]] = kwargs.pop("pids_to_profile")

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
    def pids_to_profile(self) -> Optional[List[int]]:
        return self._pids_to_profile

    def get_container_name(self, pid: int) -> str:
        if self._container_names_client is not None:
            return self._container_names_client.get_container_name(pid)
        else:
            return ""
