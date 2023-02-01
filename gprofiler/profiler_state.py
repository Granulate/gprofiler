from threading import Event
from typing import Union

from gprofiler.utils import TemporaryDirectoryWithMode


class ProfilerState:
    # Class for storing generic state parameters. These parameters are the same for each profiler.
    # Thanks to that class adding new state parameters to profilers won't result changing code in every profiler.
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
