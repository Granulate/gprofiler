#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from gprofiler.exceptions import CalledProcessError
from gprofiler.log import get_logger_adapter
from gprofiler.utils import resource_path, run_process

logger = get_logger_adapter(__name__)


def perf_path() -> str:
    return resource_path("perf")


def can_i_use_perf_events() -> bool:
    # checks access to perf_events
    # TODO invoking perf has a toll of about 1 second on my box; maybe we want to directly call
    # perf_event_open here for this test?
    try:
        run_process([perf_path(), "record", "-o", "/dev/null", "--", "/bin/true"])
    except CalledProcessError as e:
        # perf's output upon start error (e.g due to permissions denied error)
        if not (
            e.returncode == 255
            and (
                b"Access to performance monitoring and observability operations is limited" in e.stderr
                or b"perf_event_open(..., PERF_FLAG_FD_CLOEXEC) failed with unexpected error" in e.stderr
                or b"Permission error mapping pages.\n" in e.stderr
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
