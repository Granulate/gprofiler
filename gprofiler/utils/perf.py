#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from enum import Enum
from pathlib import Path
from threading import Event
from typing import List

from gprofiler.exceptions import CalledProcessError, PerfNoSupportedEvent
from gprofiler.log import get_logger_adapter
from gprofiler.utils import run_process
from gprofiler.utils.perf_process import PerfProcess, perf_path

logger = get_logger_adapter(__name__)


class SUPPORTED_PERF_EVENTS(Enum):  # pylint: disable=C0103
    """
    order here is crucial, the first one we try and succeed - will be used.
    keep it mind that we should always use `PERF_DEFAULT` as a first try.
    meaning - keeping with `perf`s' default is always preferred.
    """

    PERF_DEFAULT = "default"
    PERF_SW_CPU_CLOCK = "cpu-clock"
    PERF_SW_TASK_CLOCK = "task-clock"

    def perf_extra_args(self) -> List[str]:
        if self == SUPPORTED_PERF_EVENTS.PERF_DEFAULT:
            return []
        return ["-e", self.value]


def perf_default_event_works(tmp_dir: Path, stop_event: Event) -> list:
    """
    Validate that `perf record`'s default event actually collects samples.

    We've observed that on some machines the default event `perf record` chooses doesn't actually collect samples.
    And we generally would not want to change the default event chosen by `perf record`, so before
    any change we apply to collected sample event, we want to make sure that the default event
    actually collects samples, and make changes only if it doesn't.

    :param tmp_dir: working directory of this function
    :return: `perf record` extra arguments to use (e.g. `["-e", "cpu-clock"]`)
    """
    perf_process: PerfProcess
    for event in SUPPORTED_PERF_EVENTS:
        perf_script_output = ""
        try:
            perf_process = PerfProcess(
                frequency=11,
                stop_event=stop_event,
                output_path=str(tmp_dir / "perf_default_event.fp"),
                is_dwarf=False,
                inject_jit=False,
                extra_args=event.perf_extra_args(),
                processes_to_profile=None,
                switch_timeout_s=15,
                executable_args_to_profile=["sleep", "0.5"],
            )
            perf_process.start()
            perf_script_output = perf_process.wait_and_script()
            if perf_script_output != "":
                # `perf script` isn't empty, we'll use this event.
                return event.perf_extra_args()
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Failed to collect samples for perf event",
                exc_info=True,
                perf_event=event.name,
                perf_script_output=perf_script_output,
            )
        finally:
            perf_process.stop()
    raise PerfNoSupportedEvent


def can_i_use_perf_events() -> bool:
    # checks access to perf_events
    # TODO invoking perf has a toll of about 1 second on my box; maybe we want to directly call
    # perf_event_open here for this test?
    try:
        run_process([perf_path(), "record", "-o", "/dev/null", "--", "/bin/true"])
    except CalledProcessError as e:
        assert isinstance(e.stderr, str), f"unexpected type {type(e.stderr)}"

        # perf's output upon start error (e.g due to permissions denied error)
        if not (
            e.returncode == 255
            and (
                "Access to performance monitoring and observability operations is limited" in e.stderr
                or "perf_event_open(..., PERF_FLAG_FD_CLOEXEC) failed with unexpected error" in e.stderr
                or "Permission error mapping pages.\n" in e.stderr
            )
        ):
            logger.warning(
                "Unexpected perf exit code / error output, returning False for perf check anyway", exc_info=True
            )
        return False
    else:
        # all good
        return True


def valid_perf_pid(pid: int) -> bool:
    """
    perf, in some cases, reports PID 0 / -1. These are not real PIDs and we don't want to
    try and look up the processes related to them.
    """
    return pid not in (0, -1)
