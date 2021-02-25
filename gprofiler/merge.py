#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import re
from collections import Counter
from typing import Dict

logger = logging.getLogger(__name__)

SAMPLE_REGEX = re.compile(
    r"\s*(?P<comm>.+)\s+(?P<pid>[\d-]+)/(?P<tid>[\d-]+)(?:\s+\[(?P<cpu>\d+)])?\s+(?P<time>\d+\.\d+):\s+"
    r"(?:(?P<freq>\d+)\s+)?(?P<event_family>[\w-]+):(?:(?P<event>[\w-]+):)?(?P<suffix>[^\n]*)(?:\n(?P<stack>.*))?",
    re.MULTILINE | re.DOTALL,
)


def parse_collapsed(collapsed: str) -> Dict[str, int]:
    stacks: Dict[str, int] = Counter()
    for line in collapsed.splitlines():
        if line.strip() == "":
            continue
        if line.startswith("#"):
            continue
        try:
            stack, _, count = line.rpartition(" ")
            stacks[stack] += int(count)
        except Exception:
            logger.exception(f'bad stack - line="{line}"')
    return dict(stacks)


def collapse_stack(stack: str, comm: str) -> str:
    """
    Collapse a single stack from "perf".
    """
    funcs = [comm]
    for line in reversed(stack.splitlines()):
        # example line:
        # ffffffff81082227 mmput+0x57 ([kernel.kallsyms])
        words = line.split()
        sym = words[1].split("+")[0]
        # append kernel annotation
        if "kernel" in words[-1] or "vmlinux" in words[-1]:
            sym += "_[k]"
        funcs.append(sym)
    return ";".join(funcs)


def merge_perfs(perf_all: str, process_perfs: Dict[int, str]) -> str:
    per_process_samples: Dict[int, int] = Counter()
    new_samples: Dict[str, int] = Counter()
    process_names = {}
    for sample in perf_all.split("\n\n"):
        try:
            if sample.strip() == "":
                continue
            if sample.startswith("#"):
                continue
            match = SAMPLE_REGEX.match(sample)
            if match is None:
                raise Exception("Failed to match sample")
            parsed = match.groupdict()
            pid = int(parsed["pid"])
            if pid in process_perfs:
                per_process_samples[pid] += 1
                process_names[pid] = parsed["comm"]
            elif parsed["stack"] is not None:
                new_samples[collapse_stack(parsed["stack"], parsed["comm"])] += 1
        except Exception:
            logger.exception(f"Error processing sample: {sample}")

    for pid, perf_all_count in per_process_samples.items():
        process_stacks = parse_collapsed(process_perfs[pid])
        process_perf_count = sum(process_stacks.values())
        if process_perf_count > 0:
            ratio = perf_all_count / process_perf_count
            for stack, count in process_stacks.items():
                full_stack = ";".join([process_names[pid], stack])
                new_samples[full_stack] += round(count * ratio)

    return "\n".join((f"{stack} {count}" for stack, count in new_samples.items()))
