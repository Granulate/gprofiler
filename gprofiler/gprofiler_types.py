#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

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


ProcessToStackSampleCounters = MutableMapping[int, StackToSampleCount]
ProcessToProfileData = MutableMapping[int, ProfileData]


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


def integer_range(min_range: int, max_range: int) -> Callable[[str], int]:
    def integer_range_check(value_str: str) -> int:
        value = int(value_str)
        if value < min_range or value >= max_range:
            raise configargparse.ArgumentTypeError(
                f"invalid integer value {value!r} (out of range {min_range!r}-{max_range!r})"
            )
        return value

    return integer_range_check


def sorted_positive_int_list(s: str) -> List[int]:
    s = s.strip(" \t[]")
    if s == "":
        return []
    try:
        parts = [int(x) for x in s.split(",")]

        # Make sure the first element is positive. Next elements are required to be strictly ascending
        if parts[0] <= 0:
            raise ValueError()

        # Make sure the list is sorted in ascending order
        for a, b in zip(parts, parts[1:]):
            if a >= b:
                raise ValueError()
        return parts
    except ValueError:
        raise configargparse.ArgumentTypeError(
            f"invalid argument {s}, " "expecting a sorted list of comma-separated integers"
        )
