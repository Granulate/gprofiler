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

import re
from collections import Counter, defaultdict
from typing import Optional

from gprofiler.exceptions import CalledProcessError
from gprofiler.gprofiler_types import ProcessToStackSampleCounters
from gprofiler.log import get_logger_adapter
from gprofiler.utils import run_process
from gprofiler.utils.perf_process import perf_path

logger = get_logger_adapter(__name__)

# ffffffff81082227 mmput+0x57 ([kernel.kallsyms])
# 0 [unknown] ([unknown])
# 7fe48f00faff __poll+0x4f (/lib/x86_64-linux-gnu/libc-2.31.so)
FRAME_REGEX = re.compile(
    r"""
    ^\s*[0-9a-f]+[ ]                                 # first a hexadecimal offset
    (?P<symbol>.*)[ ]                                # a symbol name followed by a space
    \( (?:                                           # dso name is either:
        \[ (?P<dso_brackets> [^]]+) \]               # - text enclosed in square brackets, e.g.: [vdso]
        | (?P<dso_plain> [^)]+(?:[ ]\(deleted\))? )  # - OR library name, optionally followed by " (deleted)" tag
    ) \)$""",
    re.VERBOSE,
)
SAMPLE_REGEX = re.compile(
    r"\s*(?P<comm>.+?)\s+(?P<pid>[\d-]+)/(?P<tid>[\d-]+)(?:\s+\[(?P<cpu>\d+)])?\s+(?P<time>\d+\.\d+):\s+"
    r"(?:(?P<freq>\d+)\s+)?(?P<event_family>[\w\-_/]+):(?:(?P<event>[\w-]+):)?(?P<suffix>[^\n]*)(?:\n(?P<stack>.*))?",
    re.MULTILINE | re.DOTALL,
)


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


def collapse_stack(comm: str, stack: str, insert_dso_name: bool = False) -> str:
    """
    Collapse a single stack from "perf".
    """
    funcs = [comm]
    for line in reversed(stack.splitlines()):
        m = FRAME_REGEX.match(line)
        assert m is not None, f"bad line: {line}"
        sym, dso = m.group("symbol"), m.group("dso_brackets") or m.group("dso_plain")
        sym = sym.split("+")[0]  # strip the offset part.
        if sym == "[unknown]" and dso != "unknown":
            sym = f"({dso})"
        # append kernel annotation
        elif "kernel" in dso or "vmlinux" in dso:
            sym += "_[k]"
        elif insert_dso_name:
            sym += f" ({dso})"
        funcs.append(sym)
    return ";".join(funcs)


def parse_perf_script(script: Optional[str], insert_dso_name: bool = False) -> ProcessToStackSampleCounters:
    pid_to_collapsed_stacks_counters: ProcessToStackSampleCounters = defaultdict(Counter)

    if script is None:
        return pid_to_collapsed_stacks_counters

    for sample in script.split("\n\n"):
        try:
            if sample.strip() == "":
                continue
            if sample.startswith("#"):
                continue
            match = SAMPLE_REGEX.match(sample)
            if match is None:
                raise Exception("Failed to match sample")
            sample_dict = match.groupdict()

            pid = int(sample_dict["pid"])
            comm = sample_dict["comm"]
            stack = sample_dict["stack"]
            if stack is not None:
                pid_to_collapsed_stacks_counters[pid][collapse_stack(comm, stack, insert_dso_name)] += 1
        except Exception:
            logger.exception(f"Error processing sample: {sample}")
    return pid_to_collapsed_stacks_counters
