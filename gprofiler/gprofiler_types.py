#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, MutableMapping, Optional, Union

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
