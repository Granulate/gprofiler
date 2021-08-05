#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from collections import Counter
from typing import Dict, MutableMapping, Tuple

import configargparse

StackToSampleCount = Counter
ProcessToStackSampleCounters = MutableMapping[int, StackToSampleCount]
UserArgs = Dict[str, Tuple[int, bool, str]]


def positive_integer(value):
    value = int(value)
    if value <= 0:
        raise configargparse.ArgumentTypeError("invalid positive integer value: {!r}".format(value))
    return value
