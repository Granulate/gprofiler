#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import json
import logging
import math
import random
import re
import socket
from collections import Counter, defaultdict
from typing import Dict, Iterable, Mapping, MutableMapping, Optional, Tuple

from gprofiler.docker_client import DockerClient

StackToSampleCount = Counter
ProcessToStackSampleCounters = MutableMapping[int, StackToSampleCount]
ProcessIdToCommMapping = Dict[int, str]

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


def collapse_stack(comm: str, stack: str) -> str:
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
) -> Tuple[ProcessToStackSampleCounters, ProcessIdToCommMapping]:
    fp_perf, fp_pid_to_comm = parse_perf_script(raw_fp_perf)
    dwarf_perf, dwarf_pid_to_comm = parse_perf_script(raw_dwarf_perf)
    dwarf_pid_to_comm.update(fp_pid_to_comm)
    merged_pid_to_comm = dwarf_pid_to_comm

    if raw_fp_perf is None:
        return dwarf_perf, merged_pid_to_comm
    elif raw_dwarf_perf is None:
        return fp_perf, merged_pid_to_comm

    total_fp_samples = sum([sum(stacks.values()) for stacks in fp_perf.values()])
    total_dwarf_samples = sum([sum(stacks.values()) for stacks in dwarf_perf.values()])
    fp_to_dwarf_sample_ratio = total_fp_samples / total_dwarf_samples

    # The FP perf is used here as the "main" perf, to which the DWARF perf is scaled.
    merged_pid_to_stacks_counters: ProcessToStackSampleCounters = defaultdict(Counter)
    add_highest_avg_depth_stacks_per_process(
        dwarf_perf, fp_perf, fp_to_dwarf_sample_ratio, merged_pid_to_stacks_counters
    )
    total_merged_samples = sum([sum(stacks.values()) for stacks in merged_pid_to_stacks_counters.values()])
    logger.debug(
        f"Total FP samples: {total_fp_samples}; Total DWARF samples: {total_dwarf_samples}; "
        f"FP to DWARF ratio: {fp_to_dwarf_sample_ratio}; Total merged samples: {total_merged_samples}"
    )
    return merged_pid_to_stacks_counters, merged_pid_to_comm


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
        new_count = sample_count * fp_to_dwarf_sample_ratio
        # If we were to round all of the sample counts it could skew the results. By using a random factor,
        # we mostly solve this by randomly rounding up / down stacks.
        # The higher the fractional part of the new count, the more likely it is to be rounded up instead of down
        new_count = math.ceil(new_count) if new_count - int(new_count) <= random.random() else math.floor(new_count)
        if new_count == 0:
            # TODO: For more accurate truncation, check if there's a common frame for the truncated stacks and combine
            #  them
            continue
        dwarf_collapsed_stacks_counters[stack] = new_count
    # Note - returning the value is not necessary, but is done for readability
    return dwarf_collapsed_stacks_counters


def get_average_frame_count(stacks: Iterable[str]) -> float:
    frame_count_per_samples = [sample.count(";") for sample in stacks]
    return sum(frame_count_per_samples) / len(frame_count_per_samples)


def parse_perf_script(script: Optional[str]) -> Tuple[ProcessToStackSampleCounters, ProcessIdToCommMapping]:
    pid_to_collapsed_stacks_counters: ProcessToStackSampleCounters = defaultdict(Counter)
    pid_to_comm: ProcessIdToCommMapping = {}
    if script is None:
        return pid_to_collapsed_stacks_counters, pid_to_comm
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
                pid_to_collapsed_stacks_counters[pid][collapse_stack(comm, stack)] += 1
            pid_to_comm.setdefault(pid, comm)
        except Exception:
            logger.exception(f"Error processing sample: {sample}")
    return pid_to_collapsed_stacks_counters, pid_to_comm


def merge_perfs(
    system_perf_pid_to_stacks_counter: ProcessToStackSampleCounters,
    pid_to_comm: ProcessIdToCommMapping,
    process_perfs: ProcessToStackSampleCounters,
    docker_client: DockerClient,
    should_determine_container_names: bool,
) -> str:
    per_process_samples: MutableMapping[int, int] = Counter()
    new_samples: StackToSampleCount = Counter()
    for pid, stacks_counters in system_perf_pid_to_stacks_counter.items():
        if pid in process_perfs:
            per_process_samples[pid] += sum(stacks_counters.values())
        else:
            new_samples += stacks_counters

    for pid, perf_all_count in per_process_samples.items():
        process_stacks = process_perfs[pid]
        process_perf_count = sum(process_stacks.values())
        if process_perf_count > 0:
            ratio = perf_all_count / process_perf_count
            for stack, count in process_stacks.items():
                container_name = _get_container_name(pid, docker_client, should_determine_container_names)
                full_stack = ";".join([container_name, pid_to_comm[pid], stack])
                new_samples[full_stack] += round(count * ratio)
    container_names = docker_client.container_names
    docker_client.reset_cache()
    profile_metadata = {
        'containers': container_names,
        'hostname': socket.gethostname(),
        'container_names_enabled': should_determine_container_names,
    }
    output = [f"#{json.dumps(profile_metadata)}"]
    output += [f"{stack} {count}" for stack, count in new_samples.items()]
    return "\n".join(output)


def _get_container_name(pid: int, docker_client: DockerClient, should_determine_container_names: bool):
    return docker_client.get_container_name(pid) if should_determine_container_names else ""
