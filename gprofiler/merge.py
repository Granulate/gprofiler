#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import json
import math
import random
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Iterable, Optional, Tuple

from gprofiler.docker_client import DockerClient
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.metadata_type import Metadata
from gprofiler.types import ProcessToStackSampleCounters, StackToSampleCount

logger = get_logger_adapter(__name__)

SAMPLE_REGEX = re.compile(
    r"\s*(?P<comm>.+?)\s+(?P<pid>[\d-]+)/(?P<tid>[\d-]+)(?:\s+\[(?P<cpu>\d+)])?\s+(?P<time>\d+\.\d+):\s+"
    r"(?:(?P<freq>\d+)\s+)?(?P<event_family>[\w-]+):(?:(?P<event>[\w-]+):)?(?P<suffix>[^\n]*)(?:\n(?P<stack>.*))?",
    re.MULTILINE | re.DOTALL,
)

# ffffffff81082227 mmput+0x57 ([kernel.kallsyms])
# 0 [unknown] ([unknown])
# 7fe48f00faff __poll+0x4f (/lib/x86_64-linux-gnu/libc-2.31.so)
FRAME_REGEX = re.compile(r"^\s*[0-9a-f]+ (.*?) \((.*)\)$")


def parse_one_collapsed(collapsed: str, add_comm: Optional[str] = None) -> StackToSampleCount:
    """
    Parse a stack-collapsed listing.

    If 'add_comm' is not None, add it as the first frame for each stack.
    """
    stacks: StackToSampleCount = Counter()

    for line in collapsed.splitlines():
        if line.strip() == "":
            continue
        if line.startswith("#"):
            continue
        try:
            stack, _, count = line.rpartition(" ")
            if add_comm is not None:
                stacks[f"{add_comm};{stack}"] += int(count)
            else:
                stacks[stack] += int(count)
        except Exception:
            logger.exception(f'bad stack - line="{line}"')

    return stacks


def parse_and_remove_one_collapsed(collapsed: Path, add_comm: Optional[str] = None) -> StackToSampleCount:
    """
    Parse a stack-collapsed file and remove it.
    """
    data = collapsed.read_text()
    collapsed.unlink()
    return parse_one_collapsed(data, add_comm)


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
            comm_pid_tid, stack = stack.split(";", maxsplit=1)
            comm, pid_tid = comm_pid_tid.rsplit("-", maxsplit=1)
            pid = int(pid_tid.split("/")[0])
            results[pid][f"{comm};{stack}"] += int(count)
        except ValueError:
            bad_lines.append(line)

    if bad_lines:
        logger.warning(f"Got {len(bad_lines)} bad lines when parsing (showing up to 8):\n" + "\n".join(bad_lines[:8]))

    return results


def _collapse_stack(comm: str, stack: str) -> str:
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


def merge_global_perfs(raw_fp_perf: Optional[str], raw_dwarf_perf: Optional[str]) -> ProcessToStackSampleCounters:
    fp_perf = _parse_perf_script(raw_fp_perf)
    dwarf_perf = _parse_perf_script(raw_dwarf_perf)

    if raw_fp_perf is None:
        return dwarf_perf
    elif raw_dwarf_perf is None:
        return fp_perf

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
    return merged_pid_to_stacks_counters


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

        fp_frame_count_average = _get_average_frame_count(fp_collapsed_stacks_counters.keys())
        dwarf_collapsed_stacks_counters = dwarf_perf[pid]
        dwarf_frame_count_average = _get_average_frame_count(dwarf_collapsed_stacks_counters.keys())
        if fp_frame_count_average > dwarf_frame_count_average:
            merged_pid_to_stacks_counters[pid] = fp_collapsed_stacks_counters
        else:
            dwarf_collapsed_stacks_counters = scale_dwarf_samples_count(
                dwarf_collapsed_stacks_counters, fp_to_dwarf_sample_ratio
            )
            merged_pid_to_stacks_counters[pid] = dwarf_collapsed_stacks_counters


def scale_dwarf_samples_count(
    dwarf_collapsed_stacks_counters: StackToSampleCount,
    fp_to_dwarf_sample_ratio: float,
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


def _get_average_frame_count(stacks: Iterable[str]) -> float:
    frame_count_per_samples = [sample.count(";") for sample in stacks]
    return sum(frame_count_per_samples) / len(frame_count_per_samples)


def _parse_perf_script(script: Optional[str]) -> ProcessToStackSampleCounters:
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
                pid_to_collapsed_stacks_counters[pid][_collapse_stack(comm, stack)] += 1
        except Exception:
            logger.exception(f"Error processing sample: {sample}")
    return pid_to_collapsed_stacks_counters


def _make_profile_metadata(docker_client: Optional[DockerClient], add_container_names: bool, metadata: Metadata) -> str:
    if docker_client is not None and add_container_names:
        container_names = docker_client.container_names
        docker_client.reset_cache()
        enabled = True
    else:
        container_names = []
        enabled = False

    profile_metadata = {
        'containers': container_names,
        'container_names_enabled': enabled,
    }
    profile_metadata["metadata"] = metadata
    formatted_profile_metadata = "# " + json.dumps(profile_metadata)
    formatted_profile_metadata = formatted_profile_metadata.replace('\n', ' ')
    return formatted_profile_metadata


def _get_container_name(pid: int, docker_client: Optional[DockerClient], add_container_names: bool):
    return docker_client.get_container_name(pid) if add_container_names and docker_client is not None else ""


def concatenate_profiles(
    process_profiles: ProcessToStackSampleCounters,
    docker_client: Optional[DockerClient],
    add_container_names: bool,
    metadata: Metadata,
) -> Tuple[str, int]:
    """
    Concatenate all stacks from all stack mappings in process_profiles.
    Add "profile metadata" as the first line of the resulting collapsed file. Also,
    prepend the container name (if requested & available) as the first frame of each
    line.
    """
    total_samples = 0
    lines = []

    for pid, stacks in process_profiles.items():
        container_name = _get_container_name(pid, docker_client, add_container_names)
        for stack, count in stacks.items():
            total_samples += count
            lines.append(f"{container_name + ';' if add_container_names else ''}{stack} {count}")

    lines.insert(0, _make_profile_metadata(docker_client, add_container_names, metadata))
    return "\n".join(lines), total_samples


def merge_profiles(
    perf_pid_to_stacks_counter: ProcessToStackSampleCounters,
    process_profiles: ProcessToStackSampleCounters,
    docker_client: Optional[DockerClient],
    add_container_names: bool,
    metadata: Metadata,
) -> Tuple[str, int]:
    # merge process profiles into the global perf results.
    for pid, stacks in process_profiles.items():
        if len(stacks) == 0:
            # no samples collected by the runtime profiler for this process (empty stackcollapse file)
            continue

        process_perf = perf_pid_to_stacks_counter.get(pid)
        if process_perf is None:
            # no samples collected by perf for this process.
            continue

        perf_samples_count = sum(process_perf.values())
        profile_samples_count = sum(stacks.values())
        assert profile_samples_count > 0

        # do the scaling by the ratio of samples: samples we received from perf for this process,
        # divided by samples we received from the runtime profiler of this process.
        ratio = perf_samples_count / profile_samples_count
        for stack in stacks:
            stacks[stack] = round(stacks[stack] * ratio)

        # swap them: use the samples from the runtime profiler.
        perf_pid_to_stacks_counter[pid] = stacks

    return concatenate_profiles(perf_pid_to_stacks_counter, docker_client, add_container_names, metadata)
