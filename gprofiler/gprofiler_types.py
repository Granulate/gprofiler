#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from collections import Counter
from typing import MutableMapping

StackToSampleCount = Counter
ProcessToStackSampleCounters = MutableMapping[int, StackToSampleCount]
