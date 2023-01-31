from threading import Event


class ProfilerState:
    def __init__(
        self,
        stop_event: Event,
        storage_dir: str,
        profile_spawned_processes: bool,
        insert_dso_name: bool,
        profiling_mode: str,
    ) -> None:
        self.stop_event = stop_event
        self.storage_dir = storage_dir
        self.profile_spawned_processes = profile_spawned_processes
        self.insert_dso_name = insert_dso_name
        self.profiling_mode = profiling_mode
