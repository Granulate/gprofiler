from threading import Event


class ProfilerState:
    def __init__(
        self,
        stop_event: Event,
        storage_dir: str,
        profile_spawned_processes: bool,
    ) -> None:
        self.stop_event = stop_event
        self.storage_dir = storage_dir
        self.profile_spawned_processes = profile_spawned_processes
