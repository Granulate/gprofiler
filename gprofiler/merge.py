#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import re
from collections import Counter, defaultdict
from typing import Dict, Iterable, Mapping, MutableMapping, Optional, Tuple

StackToSampleCount = MutableMapping[str, int]
ProcessToStackSampleCounters = MutableMapping[int, StackToSampleCount]
ProcessIdToNameMapping = Dict[int, str]

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
    stacks: StackToSampleCount = Counter()
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


def parse_many_collapsed(text: str) -> ProcessToStackSampleCounters:
    """
    Parse a stack-collapsed listing where stacks are prefixed with the command and pid/tid of their
    origin.
    """
    results: ProcessToStackSampleCounters = defaultdict(Counter)
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


def merge_global_perfs(
    raw_fp_perf: Optional[str], raw_dwarf_perf: Optional[str]
) -> Tuple[ProcessToStackSampleCounters, ProcessIdToNameMapping]:
    merged_pid_to_stacks_counters: ProcessToStackSampleCounters = defaultdict(Counter)
    fp_perf, fp_pid_to_name = parse_perf_script(raw_fp_perf)
    dwarf_perf, dwarf_pid_to_name = parse_perf_script(raw_dwarf_perf)
    dwarf_pid_to_name.update(fp_pid_to_name)
    merged_pid_to_name = dwarf_pid_to_name

    if raw_fp_perf is None:
        return dwarf_perf, merged_pid_to_name
    elif raw_dwarf_perf is None:
        return fp_perf, merged_pid_to_name

    total_fp_samples = sum([sum(stacks.values()) for stacks in fp_perf.values()])
    total_dwarf_samples = sum([sum(stacks.values()) for stacks in dwarf_perf.values()])
    fp_to_dwarf_sample_ratio = total_fp_samples / total_dwarf_samples

    # The FP perf is used here as the "main" perf, to which the DWARF perf is scaled.
    add_highest_avg_depth_stacks_per_process(
        dwarf_perf, fp_perf, fp_to_dwarf_sample_ratio, merged_pid_to_stacks_counters
    )
    add_missing_dwarf_stacks(dwarf_perf, fp_to_dwarf_sample_ratio, merged_pid_to_stacks_counters)
    total_merged_samples = sum([sum(stacks.values()) for stacks in merged_pid_to_stacks_counters.values()])
    logger.debug(f"Total FP samples: {total_fp_samples}; Total DWARF samples: {total_dwarf_samples}; "
                 f"FP to DWARF ratio: {fp_to_dwarf_sample_ratio}; Total merged samples: {total_merged_samples}")
    return merged_pid_to_stacks_counters, merged_pid_to_name


def add_highest_avg_depth_stacks_per_process(
    dwarf_perf: ProcessToStackSampleCounters,
    fp_perf: ProcessToStackSampleCounters,
    fp_to_dwarf_sample_ratio: float,
    merged_pid_to_stacks_counters: ProcessToStackSampleCounters,
):
    for pid, fp_collapsed_stacks_counters in fp_perf.items():
        if pid not in dwarf_perf:
            merged_pid_to_stacks_counters[pid] = fp_collapsed_stacks_counters
            continue

        fp_frame_count_average = get_average_frame_count(fp_collapsed_stacks_counters.keys())
        dwarf_collapsed_stacks_counters = dwarf_perf[pid]
        dwarf_frame_count_average = get_average_frame_count(dwarf_collapsed_stacks_counters.keys())
        if fp_frame_count_average > dwarf_frame_count_average:
            merged_pid_to_stacks_counters[pid] = fp_collapsed_stacks_counters
        else:
            dwarf_collapsed_stacks_counters = scale_dwarf_samples_count(
                dwarf_collapsed_stacks_counters, fp_to_dwarf_sample_ratio
            )
            merged_pid_to_stacks_counters[pid] = dwarf_collapsed_stacks_counters


def scale_dwarf_samples_count(
    dwarf_collapsed_stacks_counters: StackToSampleCount, fp_to_dwarf_sample_ratio: float
) -> StackToSampleCount:
    if fp_to_dwarf_sample_ratio == 1:
        return dwarf_collapsed_stacks_counters
    # scale the dwarf stacks to the FP stacks to avoid skewing the results
    for stack, sample_count in dwarf_collapsed_stacks_counters.items():
        dwarf_collapsed_stacks_counters[stack] = max(1, round(sample_count * fp_to_dwarf_sample_ratio))
    # Note - returning the value is not necessary, but is done for readability
    return dwarf_collapsed_stacks_counters


def get_average_frame_count(stacks: Iterable[str]) -> float:
    frame_count_per_samples = [sample.count(";") for sample in stacks]
    return sum(frame_count_per_samples) / len(frame_count_per_samples)


def add_missing_dwarf_stacks(
    dwarf_perf: ProcessToStackSampleCounters,
    fp_to_dwarf_sample_ratio: float,
    merged_pid_to_stacks_counters: ProcessToStackSampleCounters,
):
    for pid, dwarf_collapsed_stacks_counters in dwarf_perf.items():
        if pid in merged_pid_to_stacks_counters:
            continue
        dwarf_collapsed_stacks_counters = scale_dwarf_samples_count(
            dwarf_collapsed_stacks_counters, fp_to_dwarf_sample_ratio
        )
        merged_pid_to_stacks_counters[pid] = dwarf_collapsed_stacks_counters


def parse_perf_script(script: Optional[str]) -> Tuple[ProcessToStackSampleCounters, ProcessIdToNameMapping]:
    pid_to_collapsed_stacks_counters: ProcessToStackSampleCounters = defaultdict(Counter)
    pid_to_name: ProcessIdToNameMapping = {}
    if script is None:
        return pid_to_collapsed_stacks_counters, pid_to_name
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
            process_name = sample_dict["comm"]
            stack = sample_dict["stack"]
            if stack is not None:
                pid_to_collapsed_stacks_counters[pid][collapse_stack(stack, process_name)] += 1
            pid_to_name.setdefault(pid, process_name)
        except Exception:
            logger.exception(f"Error processing sample: {sample}")
    return pid_to_collapsed_stacks_counters, pid_to_name


def merge_perfs(
    system_perf_pid_to_stacks_counter: ProcessToStackSampleCounters,
    pid_to_name: ProcessIdToNameMapping,
    process_perfs: ProcessToStackSampleCounters,
) -> str:
    per_process_samples: MutableMapping[int, int] = Counter()
    new_samples: StackToSampleCount = Counter()
    for pid, stacks_counters in system_perf_pid_to_stacks_counter.items():
        if pid in process_perfs:
            per_process_samples[pid] += sum(stacks_counters.values())
            continue
        new_samples += stacks_counters  # type: ignore

    for pid, perf_all_count in per_process_samples.items():
        process_stacks = process_perfs[pid]
        process_perf_count = sum(process_stacks.values())
        if process_perf_count > 0:
            ratio = perf_all_count / process_perf_count
            for stack, count in process_stacks.items():
                full_stack = ";".join([pid_to_name[pid], stack])
                new_samples[full_stack] += round(count * ratio)

    return "\n".join((f"{stack} {count}" for stack, count in new_samples.items()))
