#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import re
from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, MutableMapping, Optional, Union

import configargparse

StackToSampleCount = Counter
UserArgs = Dict[str, Optional[Union[int, bool, str]]]
AppMetadata = Dict[str, Any]


@dataclass
class ProfileData:
    """
    Represents data collected by gProfiler about a process.
    First and foremost are the stacks - the raw profiling data itself.
    Then we have other "metadata"-ish fields like the appid and the app metadata, and more
    will come.
    """

    stacks: StackToSampleCount
    appid: Optional[str]
    app_metadata: Optional[AppMetadata]
    container_name: Optional[str]


ProcessToStackSampleCounters = MutableMapping[int, StackToSampleCount]
ProcessToProfileData = MutableMapping[int, ProfileData]


class ProfilingErrorStack(StackToSampleCount):
    PROFILING_ERROR_STACK_PATTERN = re.compile(r".*;\[Profiling .+: .+\]")

    def __init__(self, what: str, reason: str, comm: str):
        super().__init__()
        self.update({f"{comm};[Profiling {what}: {reason}]": 1})
        assert self.is_error_stack(self)

    @staticmethod
    def is_error_stack(stack: StackToSampleCount) -> bool:
        return (
            len(stack) == 1 and ProfilingErrorStack.PROFILING_ERROR_STACK_PATTERN.match(next(iter(stack))) is not None
        )

    @staticmethod
    def attach_error_to_stacks(
        source_stacks: StackToSampleCount, error_stack: StackToSampleCount
    ) -> StackToSampleCount:
        _, error_frame = next(iter(error_stack)).split(";", maxsplit=1)
        dest_stacks: StackToSampleCount = StackToSampleCount()
        for frame, count in source_stacks.items():
            comm, stack = frame.split(";", maxsplit=1)
            annotated = f"{comm};{error_frame};{stack}"
            dest_stacks[annotated] = count
        return dest_stacks


def positive_integer(value_str: str) -> int:
    value = int(value_str)
    if value <= 0:
        raise configargparse.ArgumentTypeError("invalid positive integer value: {!r}".format(value))
    return value


def nonnegative_integer(value_str: str) -> int:
    value = int(value_str)
    if value < 0:
        raise configargparse.ArgumentTypeError("invalid non-negative integer value: {!r}".format(value))
    return value


def integers_list(value_str: str) -> List[int]:
    try:
        pids = [int(pid) for pid in value_str.split(",")]
    except ValueError:
        raise configargparse.ArgumentTypeError(
            "Integer list should be a single integer, or comma separated list of integers f.e. 13,452,2388"
        )
    return pids


def integer_range(min_range: int, max_range: int) -> Callable[[str], int]:
    def integer_range_check(value_str: str) -> int:
        value = int(value_str)
        if value < min_range or value >= max_range:
            raise configargparse.ArgumentTypeError(
                f"invalid integer value {value!r} (out of range {min_range!r}-{max_range!r})"
            )
        return value

    return integer_range_check
