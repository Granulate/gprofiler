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
from typing import List, Optional, Pattern, cast

from gprofiler.exceptions import StopEventSetException
from gprofiler.gprofiler_types import ProcessToProfileData, ProcessToStackSampleCounters, ProfileData
from gprofiler.log import get_logger_adapter
from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.profiler_base import ProfilerBase
from gprofiler.profilers.registry import ProfilerArgument, register_profiler
from gprofiler.utils import random_prefix, reap_process, resource_path, start_process, wait_event

logger = get_logger_adapter(__name__)
# Currently tracing only php-fpm, TODO: support mod_php in apache.
DEFAULT_PROCESS_FILTER = "php-fpm"


@register_profiler(
    "PHP",
    possible_modes=["phpspy", "disabled"],
    supported_archs=["x86_64", "aarch64"],
    default_mode="disabled",
    profiler_arguments=[
        ProfilerArgument(
            "--php-proc-filter",
            help="Process filter for php processes (default: %(default)s)",
            dest="php_process_filter",
            default=DEFAULT_PROCESS_FILTER,
        )
    ],
    supported_profiling_modes=["cpu"],
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
        profiler_state: ProfilerState,
        php_process_filter: str,
        php_mode: str,
    ):
        assert php_mode == "phpspy", "PHP profiler should not be initialized, wrong php_mode value given"
        super().__init__(frequency, duration, profiler_state)
        self._process: Optional[Popen] = None
        self._output_path = Path(self._profiler_state.storage_dir) / f"phpspy.{random_prefix()}.col"
        self._process_filter = php_process_filter

    def start(self) -> None:
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
        process = start_process(cmd, env=env)
        # Executing phpspy, expecting the output file to be created, phpspy creates it at bootstrap after argument
        # parsing.
        # If an error occurs after this stage it's probably a spied _process specific and not phpspy general error.
        try:
            wait_event(self.poll_timeout, self._profiler_state.stop_event, lambda: os.path.exists(self._output_path))
        except TimeoutError:
            process.kill()
            assert process.stdout is not None and process.stderr is not None
            logger.error(f"phpspy failed to start. stdout {process.stdout.read()!r} stderr {process.stderr.read()!r}")
            raise
        else:
            self._process = process

        # Set the stderr fd as non-blocking so the read operation on it won't block if no data is available.
        assert self._process.stderr is not None
        fcntl.fcntl(
            self._process.stderr.fileno(),
            fcntl.F_SETFL,
            fcntl.fcntl(self._process.stderr.fileno(), fcntl.F_GETFL) | os.O_NONBLOCK,
        )

        # Ignoring type since _process.stderr is typed as Optional[IO[Any]] which doesn't have the `read1` method.
        stderr = self._process.stderr.read1().decode()  # type: ignore
        logger.debug("phpspy stderr", stderr=self._filter_phpspy_stderr(stderr))

    def _dump(self) -> Path:
        assert self._process is not None, "profiling not started!"
        self._process.send_signal(self.dump_signal)
        # important to not grab the transient data file
        while True:
            output_files = glob.glob(f"{str(self._output_path)}.*")
            if output_files:
                break

            if self._profiler_state.stop_event.wait(0.1):
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
    def _parse_phpspy_output(cls, phpspy_output: str, profiler_state: ProfilerState) -> ProcessToProfileData:
        def extract_metadata_section(re_expr: Pattern, metadata_line: str) -> str:
            match = re_expr.match(metadata_line)
            if not match:
                raise CorruptedPHPSpyOutputException(
                    f"Couldn't extract metadata via regex '{re_expr.pattern}', line '{metadata_line}'"
                )
            return cast(str, match.group(1))

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

        profiles: ProcessToProfileData = {}
        for pid in results:
            # Because of https://github.com/Granulate/gprofiler/issues/763,
            # for now we only filter output of phpspy to return only profiles from chosen pids
            if profiler_state.processes_to_profile is not None:
                if pid not in [process.pid for process in profiler_state.processes_to_profile]:
                    continue
            # TODO: appid & app metadata for php!
            appid = None
            app_metadata = None
            profiles[pid] = ProfileData(results[pid], appid, app_metadata, profiler_state.get_container_name(pid))

        return profiles

    def snapshot(self) -> ProcessToProfileData:
        if self._profiler_state.stop_event.wait(self._duration):
            raise StopEventSetException()
        stderr = self._process.stderr.read1().decode()  # type: ignore
        logger.debug("phpspy stderr", stderr=self._filter_phpspy_stderr(stderr))

        phpspy_output_path = self._dump()
        phpspy_output_text = phpspy_output_path.read_text()
        phpspy_output_path.unlink()
        return self._parse_phpspy_output(phpspy_output_text, self._profiler_state)

    def stop(self) -> None:
        if self._process is not None:
            self._process.terminate()
            exit_code, stdout, stderr = reap_process(self._process)
            self._process = None
            logger.info(
                "Finished profiling PHP processes with phpspy",
                exit_code=exit_code,
                stdout=stdout.decode(),
                stderr=self._filter_phpspy_stderr(stderr.decode()),
            )

    def _filter_phpspy_stderr(self, stderr: str) -> str:
        skip_re = self._get_stderr_skip_regex()
        log_lines = [line for line in stderr.splitlines() if skip_re.search(line) is None]
        return "\n".join(log_lines)

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
