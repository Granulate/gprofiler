#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from collections import Counter
from typing import Dict, MutableMapping, Optional, Tuple, Union

import configargparse

StackToSampleCount = Counter
ProcessToStackSampleCounters = MutableMapping[int, StackToSampleCount]
UserArgs = Dict[str, Optional[Union[int, bool, str]]]


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
