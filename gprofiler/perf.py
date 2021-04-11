#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import os
import signal
from pathlib import Path
from threading import Event
from typing import Iterable, Mapping, Optional

import psutil

from gprofiler.exceptions import CalledProcessError
from gprofiler.merge import parse_perf_script
from gprofiler.utils import (
    TEMPORARY_STORAGE_PATH,
    poll_process,
    resource_path,
    run_process,
    start_process,
    wait_for_file,
)

logger = logging.getLogger(__name__)

PERF_BUILDID_DIR = os.path.join(TEMPORARY_STORAGE_PATH, "perf-buildids")


# TODO: base on ProfilerBase, currently can't because the snapshot() API differs here.
class SystemProfiler:
    dump_timeout = 5  # seconds
    poll_timeout = 5  # seconds

    def __init__(self, frequency: int, duration: int, stop_event: Event, storage_dir: str):
        logger.info(f"Initializing system profiler (frequency: {frequency}hz, duration: {duration}s)")
        self._perf_cmd = [resource_path("perf"), "--buildid-dir", PERF_BUILDID_DIR]
        self._frequency = frequency
        self._duration = duration
        self._stop_event = stop_event
        self._storage_dir = storage_dir
        self.output_path = storage_dir + '/perf.data'
        self.process = None

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.stop()

    def start(self, dwarf=False):
        # TODO: run dwarf in parallel, after supporting it in merge.py
        # Alternatively: fix golang dwarf problems and the run just dwarf

        # if get_cpu_count() > 32:
        #     dwarf_frequency = 99
        # else:
        #     dwarf_frequency = 999

        # logger.info("Running global perf with dwarf...")
        # run_perf("global_debug", dwarf_frequency, dwarf=True)

        logger.info("Running global perf...")
        args = ["-F", str(self._frequency), "-a", "-g", "-o", self.output_path, '--switch-output=signal']
        if dwarf:
            args += ["--call-graph", "dwarf"]
        process = start_process(self._perf_cmd + ["record"] + args)
        # Wait until the transient data file appears. That will indicate that the perf session has started.
        try:
            wait_for_file(self.output_path, self.poll_timeout, self._stop_event)
        except TimeoutError:
            process.kill()
            logger.error(f"perf failed to start. stdout {process.stdout.read()!r} stderr {process.stderr.read()!r}")
            raise
        else:
            self.process = process

    def _dump(self) -> Path:
        assert self.process is not None, "profiling not started!"
        self.process.send_signal(signal.SIGUSR2)
        try:
            # important to not grab the transient perf.data file
            return wait_for_file(f'{self.output_path}.*', self.dump_timeout, self._stop_event)
        except TimeoutError:
            logger.warning("perf dump is taking longer than expected...")
            return None
        finally:
            logger.debug(f"perf output: {self.process.stderr.read1(4096)}")

    def _process_error(self):
        stdout = self.process.stdout.read().decode()
        stderr = self.process.stderr.read().decode()
        raise CalledProcessError(self.process.returncode, self.process.args, stdout, stderr)

    def _check_free_space(self):
        free_disk = psutil.disk_usage(self._storage_dir).free
        if free_disk < 4 * 1024 * 1024:
            raise Exception(f"Free disk space: {free_disk}kb. Skipping perf!")

    def _perf_script(self, record_file: Path) -> str:
        parsed_path = os.path.join(self._storage_dir, "global.parsed")
        with open(parsed_path, "w+") as f:
            run_process(self._perf_cmd + ["script", "-F", "+pid", "-i", str(record_file)], stdout=f)
            f.seek(0)
            script = f.read()
        os.unlink(parsed_path)
        os.unlink(record_file)
        return script

    def snapshot(self) -> Iterable[Mapping[str, str]]:
        try:
            poll_process(self.process, self._duration, self._stop_event)
        except TimeoutError:
            # perf is still alive. We can proceed with dump.
            record_file = self._dump()
        else:
            self._process_error()

        self._check_free_space()
        script = self._perf_script(record_file)
        return parse_perf_script(script)

    def _terminate(self) -> Optional[int]:
        code = None
        if self.process is not None:
            self.process.terminate()  # okay to call even if process is already dead
            code = self.process.wait()
            self.process = None
        return code

    def stop(self):
        code = self._terminate()
        if code is not None:
            logger.info("Finished running global perf")
