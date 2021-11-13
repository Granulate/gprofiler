#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import fcntl
import glob
import os
import re
import signal
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path
from subprocess import Popen
from threading import Event
from typing import List, Optional, Pattern

from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import ProcessToStackSampleCounters
from gprofiler.log import get_logger_adapter
from gprofiler.profilers.profiler_base import ProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import random_prefix, resource_path, start_process, wait_event

logger = get_logger_adapter(__name__)
# Currently tracing only php-fpm, TODO: support mod_php in apache.
DEFAULT_PROCESS_FILTER = "php-fpm"


@register_profiler(
    "PHP",
    possible_modes=["phpspy", "disabled"],
    supported_archs=["x86_64"],  # we don't build phpspy for others yet
    default_mode="disabled",
    profiler_arguments=[
        ProfilerArgument(
            "--php-proc-filter",
            help="Process filter for php processes (default: %(default)s)",
            dest="php_process_filter",
            default=DEFAULT_PROCESS_FILTER,
        )
    ],
)
class PHPSpyProfiler(ProfilerBase):
    PHPSPY_RESOURCE = "php/phpspy"
    dump_signal = signal.SIGUSR2
    poll_timeout = 10  # seconds
    MAX_FREQUENCY = 999
    MIN_DURATION = 3  # seconds, phpspy is running commands at bootstrap and it takes some time.
    BUFFER_SIZE = 16384
    NUM_WORKERS = 16  # Num of workers following unique PHP processes (one worker per _process)

    # regex required for parsing phpspy output
    SINGLE_FRAME_RE = re.compile(r"^(?P<f_index>\d+) (?P<line>.*)$")  # "1 main.php:24"
    PID_METADATA_RE = re.compile(r"^# pid = (.*)$")  # "# pid = 455"
    PHP_FRAME_ANNOTATION = "[php]"

    def __init__(
        self,
        frequency: int,
        duration: int,
        stop_event: Optional[Event],
        storage_dir: str,
        php_process_filter: str,
        php_mode: str,
    ):
        assert php_mode == "phpspy", "PHP profiler should not be initialized, wrong php_mode value given"
        super().__init__(frequency, duration, stop_event, storage_dir)
        self._process: Optional[Popen] = None
        self._output_path = Path(self._storage_dir) / f"phpspy.{random_prefix()}.col"
        self._process_filter = php_process_filter

    def start(self):
        logger.info("Starting profiling of PHP processes with phpspy")
        phpspy_path = resource_path(self.PHPSPY_RESOURCE)
        cmd = [
            phpspy_path,
            "--verbose-fields=p",  # output pid
            "-P",
            self._process_filter,
            "-H",
            str(self._frequency),
            "-b",
            str(self.BUFFER_SIZE),
            "-T",
            str(self.NUM_WORKERS),
            "--output",
            str(self._output_path),
            # Duration is irrelevant here, we want to run continuously.
        ]

        # importlib.resources doesn't provide a way to get a directory because it's not "a resource",
        # we use the same dir for required binaries, if they are not available.
        phpspy_dir = os.path.dirname(phpspy_path)
        env = os.environ.copy()
        env["PATH"] = f"{env.get('PATH')}:{phpspy_dir}"
        process = start_process(cmd, env=env, via_staticx=False)
        # Executing phpspy, expecting the output file to be created, phpspy creates it at bootstrap after argument
        # parsing.
        # If an error occurs after this stage it's probably a spied _process specific and not phpspy general error.
        try:
            wait_event(self.poll_timeout, self._stop_event, lambda: os.path.exists(self._output_path))
        except TimeoutError:
            process.kill()
            assert process.stdout is not None and process.stderr is not None
            logger.error(f"phpspy failed to start. stdout {process.stdout.read()!r} stderr {process.stderr.read()!r}")
            raise
        else:
            self._process = process

        # Set the stderr fd as non-blocking so the read operation on it won't block if no data is available.
        fcntl.fcntl(
            self._process.stderr.fileno(),
            fcntl.F_SETFL,
            fcntl.fcntl(self._process.stderr.fileno(), fcntl.F_GETFL) | os.O_NONBLOCK,
        )

        # Ignoring type since _process.stderr is typed as Optional[IO[Any]] which doesn't have the `read1` method.
        stderr = self._process.stderr.read1(1024).decode()  # type: ignore
        self._process_stderr(stderr)

    def _dump(self) -> Path:
        assert self._process is not None, "profiling not started!"
        self._process.send_signal(self.dump_signal)
        # important to not grab the transient data file
        while True:
            output_files = glob.glob(f"{str(self._output_path)}.*")
            if output_files:
                break

            if self._stop_event.wait(0.1):
                raise StopEventSetException()

        # All the snapshot samples should be in a single file
        assert len(output_files) == 1, "expected single file but got: " + str(output_files)
        return Path(output_files[0])

    @classmethod
    def _collapse_frames(cls, raw_frames: List[str]) -> str:
        parsed_frames = []
        for idx, raw_frame in enumerate(raw_frames):
            match = cls.SINGLE_FRAME_RE.match(raw_frame)
            if not match:
                raise CorruptedPHPSpyOutputException(f"Line {idx} ({raw_frame}) didn't match known re pattern")

            if int(match.group("f_index")) != idx:
                raise CorruptedPHPSpyOutputException(
                    f"phpspy reported index {match.group('f_index')} doesn't match line index ({idx})"
                )

            parsed_frames.append(f"{match.group('line')}_{cls.PHP_FRAME_ANNOTATION}")
        # add the "comm" - currently just "php", until we complete https://github.com/Granulate/phpspy/pull/3
        # to get the real comm.
        parsed_frames.append("php")
        return ";".join(reversed(parsed_frames))

    @classmethod
    def _parse_phpspy_output(cls, phpspy_output: str) -> ProcessToStackSampleCounters:
        def extract_metadata_section(re_expr: Pattern, metadata_line: str) -> str:
            match = re_expr.match(metadata_line)
            if not match:
                raise CorruptedPHPSpyOutputException(
                    f"Couldn't extract metadata via regex '{re_expr.pattern}', line '{metadata_line}'"
                )
            return match.group(1)

        results: ProcessToStackSampleCounters = defaultdict(Counter)

        stacks = phpspy_output.split("\n\n")  # Last part is always empty.
        last_stack, stacks = stacks[-1], stacks[:-1]
        if last_stack != "":
            logger.warning(f"phpspy output: last stack is not empty - '{last_stack}'")

        corrupted_stacks = 0
        for stack in stacks:
            try:
                frames = stack.split("\n")
                # Last line is the PID.
                pid_raw = frames.pop(-1)
                pid = int(extract_metadata_section(cls.PID_METADATA_RE, pid_raw))
                collapsed_frames = cls._collapse_frames(frames)
                results[pid][collapsed_frames] += 1

            except CorruptedPHPSpyOutputException:
                logger.exception(stack)
                corrupted_stacks += 1

            except Exception:
                corrupted_stacks += 1
                logger.exception("Unknown exception caught while parsing a phpspy stack, continuing to the next stack")

        if corrupted_stacks > 0:
            logger.warning(f"phpspy: {corrupted_stacks} corrupted stacks")

        return dict(results)

    def snapshot(self) -> ProcessToStackSampleCounters:
        if self._stop_event.wait(self._duration):
            raise StopEventSetException()
        stderr = self._process.stderr.read1(1024).decode()  # type: ignore
        self._process_stderr(stderr)

        phpspy_output_path = self._dump()
        phpspy_output_text = phpspy_output_path.read_text()
        phpspy_output_path.unlink()
        return self._parse_phpspy_output(phpspy_output_text)

    def _terminate(self) -> Optional[int]:
        code = None
        if self._process is not None:
            self._process.terminate()
            code = self._process.wait()
            self._process = None
        return code

    def stop(self):
        code = self._terminate()
        if code is not None:
            logger.info("Finished profiling PHP processes with phpspy")

    def _process_stderr(self, stderr: str):
        skip_re = self._get_stderr_skip_regex()
        lines = stderr.splitlines()
        for line in lines:
            if not skip_re.search(line):
                logger.debug(f"phpspy: error: {line}")

    @staticmethod
    @lru_cache(maxsize=1)
    def _get_stderr_skip_regex() -> Pattern:
        skip_patterns = [
            "popen_read_line: No stdout;",  # Generic popen fail line, doesn't really mean anything
            # Many "self pgrep" log errors that will happen only on race conditions.
            "Couldn't read proc fs file",
            "Can't open file for reading",
            "Couldn't read data from file",
        ]
        groups = [f"({re.escape(pattern)})" for pattern in skip_patterns]
        return re.compile("|".join(groups))


class CorruptedPHPSpyOutputException(Exception):
    pass
