#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import re
from collections import Counter, defaultdict
from typing import Iterable, Mapping, MutableMapping

logger = logging.getLogger(__name__)

SAMPLE_REGEX = re.compile(
    r"\s*(?P<comm>.+?)\s+(?P<pid>[\d-]+)/(?P<tid>[\d-]+)(?:\s+\[(?P<cpu>\d+)])?\s+(?P<time>\d+\.\d+):\s+"
    r"(?:(?P<freq>\d+)\s+)?(?P<event_family>[\w-]+):(?:(?P<event>[\w-]+):)?(?P<suffix>[^\n]*)(?:\n(?P<stack>.*))?",
    re.MULTILINE | re.DOTALL,
)

# ffffffff81082227 mmput+0x57 ([kernel.kallsyms])
# 0 [unknown] ([unknown])
# 7fe48f00faff __poll+0x4f (/lib/x86_64-linux-gnu/libc-2.31.so)
FRAME_REGEX = re.compile(r"^\s*[0-9a-f]+ (.*?) \((.*)\)$")


def parse_one_collapsed(collapsed: str) -> Mapping[str, int]:
    """
    Parse a stack-collapsed listing where all stacks are from the same process.
    """
    stacks: MutableMapping[str, int] = Counter()
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


def parse_many_collapsed(text: str) -> Mapping[int, Mapping[str, int]]:
    """
    Parse a stack-collapsed listing where stacks are prefixed with the command and pid/tid of their
    origin.
    """
    results: MutableMapping[int, MutableMapping[str, int]] = defaultdict(Counter)
    bad_lines = []

    for line in text.splitlines():
        try:
            stack, count = line.rsplit(" ", maxsplit=1)
            head, tail = stack.split(";", maxsplit=1)
            _, pid_tid = head.rsplit("-", maxsplit=1)
            pid = int(pid_tid.split("/")[0])
            results[pid][tail] += int(count)
        except ValueError:
            bad_lines.append(line)

    if bad_lines:
        logger.warning(f"Got {len(bad_lines)} bad lines when parsing (showing up to 8):\n" + "\n".join(bad_lines[:8]))

    return results


def _collapse_stack(stack: str, comm: str) -> str:
    """
    Collapse a single stack from "perf".
    """
    funcs = [comm]
    for line in reversed(stack.splitlines()):
        m = FRAME_REGEX.match(line)
        assert m is not None, f"bad line: {line}"
        sym, dso = m.groups()
        sym = sym.split("+")[0]  # strip the offset part.
        if sym == "[unknown]" and dso != "[unknown]":
            sym = f"[{dso}]"
        # append kernel annotation
        elif "kernel" in dso or "vmlinux" in dso:
            sym += "_[k]"
        funcs.append(sym)
    return ";".join(funcs)


def parse_perf_script(script: str):
    for sample in script.split("\n\n"):
        try:
            if sample.strip() == "":
                continue
            if sample.startswith("#"):
                continue
            match = SAMPLE_REGEX.match(sample)
            if match is None:
                raise Exception("Failed to match sample")
            yield match.groupdict()
        except Exception:
            logger.exception(f"Error processing sample: {sample}")


def _concatenate_stacks(stacks_iter: Iterable[Mapping[str, int]]) -> str:
    """
    Concatenate all stacks from all stack mappings in stack_iter.
    """
    return "\n".join((f"{stack} {count}" for stacks in stacks_iter for stack, count in stacks.items()))


def merge_perfs(perf_all: Iterable[Mapping[str, str]], process_perfs: Mapping[int, Mapping[str, int]]) -> str:
    per_process_samples: MutableMapping[int, int] = Counter()
    new_samples: MutableMapping[str, int] = Counter()
    process_names = {}
    for parsed in perf_all:
        try:
            pid = int(parsed["pid"])
            if pid in process_perfs:
                per_process_samples[pid] += 1
                process_names[pid] = parsed["comm"]
            elif parsed["stack"] is not None:
                new_samples[_collapse_stack(parsed["stack"], parsed["comm"])] += 1
        except Exception:
            logger.exception(f"Error processing sample: {parsed}")

    for pid, perf_all_count in per_process_samples.items():
        process_stacks = process_perfs[pid]
        process_perf_count = sum(process_stacks.values())
        if process_perf_count > 0:
            ratio = perf_all_count / process_perf_count
            for stack, count in process_stacks.items():
                full_stack = ";".join([process_names[pid], stack])
                new_samples[full_stack] += round(count * ratio)

    return _concatenate_stacks((new_samples,))


def concatenate_perfs(process_perfs: Mapping[int, Mapping[str, int]]) -> str:
    return _concatenate_stacks(process_perfs.values())
