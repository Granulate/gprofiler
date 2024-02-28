#
# Copyright (C) 2023 Intel Corporation
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#    http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

# speedscope -> collapsed converter, aimed to work for dotnet-trace.
# speedscope spec:
# https://github.com/jlfwong/speedscope/blob/639dae322b15fbcba5cd02c90335889fd285686a/src/lib/file-format-spec.ts

import json
import math
import random
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from gprofiler.gprofiler_types import StackToSampleCount


def _speedscope_frame_name(speedscope: Dict[str, Any], frame: int) -> str:
    name = speedscope["shared"]["frames"][frame]["name"]
    assert isinstance(name, str)
    return name


def load_speedscope_as_collapsed(
    speedscope_path: str,
    frequncy_hz: int,
    add_comm: Optional[str] = None,
    frame_suffix: str = "",
) -> StackToSampleCount:
    interval = 1 / frequncy_hz
    interval_ms = interval * 1000

    with open(speedscope_path) as f:
        speedscope = json.load(f)

    result_stacks: StackToSampleCount = Counter()

    for profile in speedscope["profiles"]:  # a profile per thread
        assert profile["type"] == "evented", profile["type"]  # what dotnet-trace uses
        assert profile["unit"] == "milliseconds", profile["unit"]  # what dotnet-trace uses
        stack: List[int] = []
        stacks: List[Tuple[int, ...]] = []

        # needs to be a float, but dotnet-trace puts a string...
        last_ts = float(profile["startValue"])  # matches the ts of first event
        for event in profile["events"]:
            at = event["at"]
            elapsed = at - last_ts
            last_ts = at

            if event["type"] == "O":
                stack.append(event["frame"])
            else:
                assert event["type"] == "C", f"unexpected event type: {event['type']}"
                assert stack[-1] == event["frame"]
                stack.pop()

            frac, n = math.modf(elapsed / interval_ms)
            assert int(n) == n
            for _ in range(int(n)):
                stacks.append(tuple(stack))
            if random.random() <= frac:
                stacks.append(tuple(stack))

        for s in stacks:
            result_stacks[
                (f"{add_comm};" if add_comm is not None else "")
                + ";".join(map(lambda f: _speedscope_frame_name(speedscope, f) + frame_suffix, s))
            ] += 1
    return result_stacks
