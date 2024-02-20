#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import json
import shutil
from enum import Enum
from pathlib import Path
from typing import cast

from gprofiler.exceptions import CalledProcessError, PerfNoSupportedEvent
from gprofiler.log import get_logger_adapter
from gprofiler.utils import random_prefix, resource_path, run_process
from gprofiler.utils.fs import mkdir_owned_root

logger = get_logger_adapter(__name__)


class SUPPORTED_PERF_EVENTS(Enum):
    PERF_DEFAULT = None
    PERF_SW_CPU_CLOCK = "cpu-clock"
    PERF_SW_TASK_CLOCK = "task-clock"

    def perf_extra_args(self) -> list:
        if self == SUPPORTED_PERF_EVENTS.PERF_DEFAULT:
            return []
        return ["-e", self.value]


def perf_sanity_record_to_json(working_dir: Path, perf_record_extra_args: list) -> dict:
    """
    Converts a perf record file to a JSON string.

    Note:
        this function propogate all exceptions.

    :param working_dir: working directory of this function
    :param perf_record_extra_args: extra arguments to pass to `perf record`
    :return: `dict` representation of the recorded perf data
    """
    tmp_dir = working_dir / random_prefix()
    record_sample_file = tmp_dir / "perf.data"
    json_data_file = tmp_dir / "data.json"
    # `perf record` will record the /bin/true command to `record_sample_file`
    # e.g. `perf record -F max -g -o /tmp/tmpdir/perf.data -e cpu-clock -- /bin/true`
    perf_record_cmd = [perf_path(), "record", "-F", "max", "-g", "-o", f"{str(record_sample_file)}"]
    perf_record_cmd.extend(perf_record_extra_args)
    perf_record_cmd.extend(["--", "/bin/true"])
    # `perf data` will read from `record_sample_file` and write to `json_data_file` in JSON format
    # e.g. `perf data -i /tmp/tmpdir/perf.data convert --to-json /tmp/tmpdir/data.json`
    perf_data_cmd = [perf_path(), "data", "-i", str(record_sample_file), "convert", "--to-json", str(json_data_file)]
    try:
        mkdir_owned_root(tmp_dir)
        run_process(perf_record_cmd)
        # `perf data` will read from `record_sample_file` and write to `json_data_file`
        run_process(perf_data_cmd)
        with open(json_data_file, "r", encoding="utf-8") as file:
            return cast(dict, json.load(file))
    except json.JSONDecodeError as e:
        logger.critical(
            "Failed to parse perf data to JSON",
            exc_info=e,
            extra={"perf_data": json_data_file.read_text(encoding="latin-1")},
        )
        raise
    finally:
        # ensures cleanup
        shutil.rmtree(tmp_dir, ignore_errors=True)


def perf_default_event_works(work_directory: Path) -> list:
    """
    Validate that `perf record`'s default event actually collects samples.

    We generally would not want to change the default event chosen by `perf record`, so before
    any change we apply to collected sample event, we want to make sure that the default event
    actually collects samples.

    :param work_directory: working directory of this function
    :return: `perf record` extra arguments to use (e.g. `["-e", "cpu-clock"]`)
    """
    for event in SUPPORTED_PERF_EVENTS:
        perf_record_output: dict
        try:
            perf_record_output = perf_sanity_record_to_json(work_directory, event.perf_extra_args())
            perf_samples = perf_record_output.get("samples")
            if perf_samples:
                # We have samples, we can use this event.
                return event.perf_extra_args()
        except Exception:  # pylint: disable=broad-except
            logger.warning(
                "Failed to collect samples for perf event",
                exc_info=True,
                perf_event=event.name,
                perf_record_output=perf_record_output,
            )
    raise PerfNoSupportedEvent


def perf_path() -> str:
    return resource_path("perf")


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
