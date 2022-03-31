#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from granulate_utils.exceptions import CalledProcessError

from gprofiler.utils import resource_path, run_process_logged


def perf_path() -> str:
    return resource_path("perf")


def can_i_use_perf_events() -> bool:
    # checks access to perf_events
    # TODO invoking perf has a toll of about 1 second on my box; maybe we want to directly call
    # perf_event_open here for this test?
    try:
        run_process_logged([perf_path(), "record", "-o", "/dev/null", "--", "/bin/true"])
    except CalledProcessError as e:
        # perf's output upon start error (e.g due to permissions denied error)
        if e.returncode == 255 and (
            b"Access to performance monitoring and observability operations is limited" in e.stderr
            or b"perf_event_open(..., PERF_FLAG_FD_CLOEXEC) failed with unexpected error" in e.stderr
        ):
            return False
        raise
    else:
        # all good
        return True
