from __future__ import annotations

from dataclasses import dataclass
from threading import Event
from typing import TYPE_CHECKING, List, Optional

from psutil import Process

if TYPE_CHECKING:
    from gprofiler.containers_client import ContainerNamesClient

from gprofiler.utils import TemporaryDirectoryWithMode


@dataclass
class ProfilerState:
    # Class for storing generic state parameters. These parameters are the same for each profiler.
    # Thanks to that class adding new state parameters to profilers won't result changing code in every profiler.
    # TODO: as soon as Python3.10 is adopted, turn this dataclass into kw_only

    stop_event: Event
    storage_dir: str
    profile_spawned_processes: bool
    insert_dso_name: bool
    profiling_mode: str
    container_names_client: Optional[ContainerNamesClient]
    processes_to_profile: Optional[List[Process]]

    def __post_init__(self) -> None:
        self._temporary_dir = TemporaryDirectoryWithMode(dir=self.storage_dir, mode=0o755)
        self.storage_dir = self._temporary_dir.name

    def get_container_name(self, pid: int) -> str:
        if self.container_names_client is not None:
            return self.container_names_client.get_container_name(pid)
        else:
            return ""
