#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from collections import Counter
from dataclasses import dataclass
from typing import Any, Callable, Dict, MutableMapping, Optional, Union

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
