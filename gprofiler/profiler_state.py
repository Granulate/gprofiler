from threading import Event
from typing import Union

from gprofiler.utils import TemporaryDirectoryWithMode


class ProfilerState:
    def __init__(
        self,
        stop_event: Event,
        storage_dir: Union[TemporaryDirectoryWithMode, str],
        profile_spawned_processes: bool,
        insert_dso_name: bool,
        profiling_mode: str,
    ) -> None:
        self.stop_event = stop_event
        self.profile_spawned_processes = profile_spawned_processes
        self.insert_dso_name = insert_dso_name
        self.profiling_mode = profiling_mode
        if type(storage_dir) == TemporaryDirectoryWithMode:
            self._temporary_dir = storage_dir
            self.storage_dir = storage_dir.name
            print(type(self.storage_dir))
        else:
            self.storage_dir = storage_dir
