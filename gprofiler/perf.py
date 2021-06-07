#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import concurrent.futures
import logging
import os
from tempfile import NamedTemporaryFile
from threading import Event
from typing import Tuple

import psutil

from gprofiler.merge import ProcessIdToCommMapping, ProcessToStackSampleCounters, merge_global_perfs
from gprofiler.utils import TEMPORARY_STORAGE_PATH, resource_path, run_process

logger = logging.getLogger(__name__)

PERF_BUILDID_DIR = os.path.join(TEMPORARY_STORAGE_PATH, "perf-buildids")


# TODO: base on ProfilerBase, currently can't because the snapshot() API differs here.
class SystemProfiler:
    def __init__(
        self, frequency: int, duration: int, stop_event: Event, storage_dir: str, perf_mode: str, dwarf_stack_size
    ):
        logger.info(f"Initializing system profiler (frequency: {frequency}hz, duration: {duration}s)")
        self._frequency = frequency
        self._duration = duration
        self._stop_event = stop_event
        self._storage_dir = storage_dir
        self._fp_perf = perf_mode in ("fp", "smart")
        self._dwarf_perf = perf_mode in ("dwarf", "smart")
        self._dwarf_stack_size = dwarf_stack_size

    def start(self):
        pass

    def stop(self):
        pass

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def _run_perf(self, dwarf: bool = False) -> str:
        buildid_args = ["--buildid-dir", PERF_BUILDID_DIR]

        with NamedTemporaryFile(dir=self._storage_dir) as record_file:
            args = ["-F", str(self._frequency), "-a", "-g", "-o", record_file.name]
            if dwarf:
                args += ["--call-graph", f"dwarf,{self._dwarf_stack_size}"]
            run_process(
                [resource_path("perf")] + buildid_args + ["record"] + args + ["--", "sleep", str(self._duration)],
                stop_event=self._stop_event,
            )
            perf_script_result = run_process(
                [resource_path("perf")] + buildid_args + ["script", "-F", "+pid", "-i", record_file.name],
                suppress_log=True,
            )
            return perf_script_result.stdout.decode('utf8')

    def snapshot(self) -> Tuple[ProcessToStackSampleCounters, ProcessIdToCommMapping]:
        free_disk = psutil.disk_usage(self._storage_dir).free
        if free_disk < 4 * 1024 * 1024:
            raise Exception(f"Free disk space: {free_disk}kb. Skipping perf!")

        logger.info("Running global perf...")
        perf_result = self._get_global_perf_result()
        logger.info("Finished running global perf")
        return perf_result

    def _get_global_perf_result(self):
        if not self._fp_perf:
            return merge_global_perfs(None, self._run_perf(dwarf=True))
        if not self._dwarf_perf:
            return merge_global_perfs(self._run_perf(dwarf=False), None)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            # We are running 2 perfs in parallel - one with DWARF and one with FP, and then we merge their results.
            # This improves the results from software that is compiled without frame pointers,
            # like some native software. DWARF by itself is not good enough, as it has issues with unwinding some
            # versions of Go processes.
            fp_future = executor.submit(self._run_perf, False)
            dwarf_future = executor.submit(self._run_perf, True)
        fp_perf = fp_future.result()
        dwarf_perf = dwarf_future.result()
        return merge_global_perfs(fp_perf, dwarf_perf)
