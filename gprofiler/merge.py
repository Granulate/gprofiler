#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import os
import re
from collections import Counter, defaultdict
from typing import DefaultDict, Dict, List, Mapping, MutableMapping, Optional

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


def collapse_stack(stack: str, comm: str) -> str:
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


def merge_global_perfs(raw_fp_perf: Optional[str], raw_dwarf_perf: Optional[str]) -> Dict[int, List[Dict[str, str]]]:
    merged_perf: Dict[int, List[Dict[str, str]]] = {}
    fp_perf = parse_perf_script(raw_fp_perf)
    dwarf_perf = parse_perf_script(raw_dwarf_perf)
    for pid, fp_samples in fp_perf.items():
        if pid not in dwarf_perf:
            merged_perf[pid] = fp_samples
            continue
        fp_frame_count_average = get_average_frame_count(fp_samples)
        dwarf_samples = dwarf_perf[pid]
        dwarf_frame_count_average = get_average_frame_count(dwarf_samples)
        merged_perf[pid] = fp_samples if fp_frame_count_average > dwarf_frame_count_average else dwarf_samples
    for pid, dwarf_samples in dwarf_perf.items():
        if pid in merged_perf:
            continue
        merged_perf[pid] = dwarf_samples
    return merged_perf


def get_average_frame_count(samples: List[Dict[str, str]]) -> float:
    frame_count_per_samples = [sample["stack"].count(os.linesep) for sample in samples if sample["stack"] is not None]
    return sum(frame_count_per_samples) / len(frame_count_per_samples)


def parse_perf_script(script: Optional[str]) -> DefaultDict[int, List[Dict[str, str]]]:
    pid_to_sample_info: DefaultDict[int, List[Dict[str, str]]] = defaultdict(list)
    if script is None:
        return pid_to_sample_info
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
            pid_to_sample_info[int(sample_dict["pid"])].append(
                {"comm": sample_dict["comm"], "stack": sample_dict["stack"]}
            )
        except Exception:
            logger.exception(f"Error processing sample: {sample}")
    return pid_to_sample_info


def merge_perfs(perf_all: Dict[int, List[Dict[str, str]]], process_perfs: Mapping[int, Mapping[str, int]]) -> str:
    per_process_samples: MutableMapping[int, int] = Counter()
    new_samples: MutableMapping[str, int] = Counter()
    process_names = {}
    for pid, samples in perf_all.items():
        for sample in samples:
            try:
                if pid in process_perfs:
                    per_process_samples[pid] += 1
                    process_names[pid] = sample["comm"]
                elif sample["stack"] is not None:
                    new_samples[collapse_stack(sample["stack"], sample["comm"])] += 1
            except Exception:
                logger.exception(f"Error processing sample: {sample}")

    for pid, perf_all_count in per_process_samples.items():
        process_stacks = process_perfs[pid]
        process_perf_count = sum(process_stacks.values())
        if process_perf_count > 0:
            ratio = perf_all_count / process_perf_count
            for stack, count in process_stacks.items():
                full_stack = ";".join([process_names[pid], stack])
                new_samples[full_stack] += round(count * ratio)

    return "\n".join((f"{stack} {count}" for stack, count in new_samples.items()))
