#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import Event

import psutil

from .utils import run_process, resource_path
from .merge import parse_perf_script

logger = logging.getLogger(__name__)


class SystemProfiler:
    def __init__(self, frequency: int, duration: int, stop_event: Event, storage_dir: str):
        logger.info(f"Initializing system profiler (frequency: {frequency}hz, duration: {duration}s)")
        self._frequency = frequency
        self._duration = duration
        self._stop_event = stop_event
        self._storage_dir = storage_dir

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self):
        pass

    def run_perf(self, filename_base: str, dwarf=False):
        parsed_path = os.path.join(self._storage_dir, f"{filename_base}.parsed")

        with NamedTemporaryFile(dir=self._storage_dir) as record_file:
            args = ["-F", str(self._frequency), "-a", "-g", "-o", record_file.name]
            if dwarf:
                args += ["--call-graph", "dwarf"]
            run_process(
                [resource_path("perf"), "record"] + args + ["--", "sleep", str(self._duration)],
                stop_event=self._stop_event,
            )
            with open(parsed_path, "w") as f:
                run_process([resource_path("perf"), "script", "-F", "+pid", "-i", record_file.name], stdout=f)
            return parsed_path

    def profile(self):
        free_disk = psutil.disk_usage(self._storage_dir).free
        if free_disk < 4 * 1024 * 1024:
            raise Exception(f"Free disk space: {free_disk}kb. Skipping perf!")

        logger.info("Running global perf...")
        result = self.run_perf("global")
        logger.info("Finished running global perf")
        return parse_perf_script(Path(result).read_text())

        # TODO: run dwarf in parallel, after supporting it in merge.py
        # Alternatively: fix golang dwarf problems and the run just dwarf

        # if get_cpu_count() > 32:
        #     dwarf_frequency = 99
        # else:
        #     dwarf_frequency = 999

        # logger.info("Running global perf with dwarf...")
        # run_perf("global_debug", dwarf_frequency, dwarf=True)
