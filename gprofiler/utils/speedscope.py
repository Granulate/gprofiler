#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
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
