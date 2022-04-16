#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import json
import math
import random
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

from granulate_utils.metadata import Metadata

from gprofiler.containers_client import ContainerNamesClient
from gprofiler.gprofiler_types import ProcessToStackSampleCounters, StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.metadata import application_identifiers
from gprofiler.metadata.application_metadata import get_application_metadata
from gprofiler.metadata.enrichment import EnrichmentOptions
from gprofiler.system_metrics import Metrics

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


def parse_one_collapsed_file(collapsed: Path, add_comm: Optional[str] = None) -> StackToSampleCount:
    """
    Parse a stack-collapsed file.
    """
    return parse_one_collapsed(collapsed.read_text(), add_comm)


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
) -> None:
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
            dwarf_collapsed_stacks_counters = scale_sample_counts(
                dwarf_collapsed_stacks_counters, fp_to_dwarf_sample_ratio
            )
            merged_pid_to_stacks_counters[pid] = dwarf_collapsed_stacks_counters


def scale_sample_counts(stacks: StackToSampleCount, ratio: float) -> StackToSampleCount:
    if ratio == 1:
        return stacks

    scaled_stacks: StackToSampleCount = StackToSampleCount()
    for stack, count in stacks.items():
        new_count = count * ratio
        # If we were to round all of the sample counts it could skew the results. By using a random factor,
        # we mostly solve this by randomly rounding up / down stacks.
        # The higher the fractional part of the new count, the more likely it is to be rounded up instead of down
        scaled_value = math.ceil(new_count) if random.random() <= math.modf(new_count)[0] else math.floor(new_count)
        # TODO: For more accurate truncation, check if there's a common frame for the truncated stacks and combine them
        if scaled_value != 0:
            scaled_stacks[stack] = scaled_value
    return scaled_stacks


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


def _make_profile_metadata(
    container_names_client: Optional[ContainerNamesClient],
    add_container_names: bool,
    metadata: Metadata,
    metrics: Metrics,
    application_metadata: Optional[List[Optional[Dict]]],
    application_metadata_enabled: bool,
) -> str:
    if container_names_client is not None and add_container_names:
        container_names = container_names_client.container_names
        container_names_client.reset_cache()
        enabled = True
    else:
        container_names = []
        enabled = False

    profile_metadata = {
        "containers": container_names,
        "container_names_enabled": enabled,
        "metadata": metadata,
        "metrics": metrics.__dict__,
        "application_metadata": application_metadata,
        "application_metadata_enabled": application_metadata_enabled,
    }
    return "# " + json.dumps(profile_metadata)


def _get_container_name(
    pid: int, container_names_client: Optional[ContainerNamesClient], add_container_names: bool
) -> str:
    return (
        container_names_client.get_container_name(pid)
        if add_container_names and container_names_client is not None
        else ""
    )


@dataclass
class PidStackEnrichment:
    application_name: Optional[str]
    application_prefix: str
    container_prefix: str


def _enrich_pid_stacks(
    pid: int,
    enrichment_options: EnrichmentOptions,
    container_names_client: Optional[ContainerNamesClient],
    application_metadata: List[Optional[Dict]],
) -> PidStackEnrichment:
    """
    Enrichment per app (or, PID here). This includes:
    * appid (or app name)
    * app metadata
    * container name
    """
    # generate application name
    application_name = (
        application_identifiers.get_application_name(pid) if enrichment_options.application_identifiers else None
    )
    if application_name is not None:
        application_name = f"appid: {application_name}"

    # generate metadata
    if enrichment_options.application_metadata:
        app_metadata = get_application_metadata(pid)
    else:
        app_metadata = None
    if app_metadata not in application_metadata:
        application_metadata.append(app_metadata)
    idx = application_metadata.index(app_metadata)
    # we include the application metadata frame if application_metadata is enabled, and we're not in protocol
    # version v1.
    if enrichment_options.profile_api_version != "v1" and enrichment_options.application_metadata:
        application_prefix = f"{idx};"
    else:
        application_prefix = ""

    # generate container name
    # to maintain compatibility with old profiler versions, we include the container name frame in any case
    # if the protocol version does not "v1, regardless of whether container_names is enabled or not.
    if enrichment_options.profile_api_version != "v1":
        container_prefix = _get_container_name(pid, container_names_client, enrichment_options.container_names) + ";"
    else:
        container_prefix = ""

    return PidStackEnrichment(application_name, application_prefix, container_prefix)


def _enrich_and_finalize_stack(
    stack: str, count: int, enrichment_options: EnrichmentOptions, enrich_data: PidStackEnrichment
) -> str:
    """
    Attach the enrichment data collected for the PID of this stack.
    """
    if enrichment_options.application_identifiers and enrich_data.application_name is not None:
        # insert the app name between the first frame and all others
        try:
            first_frame, others = stack.split(";", maxsplit=1)
            stack = f"{first_frame};{enrich_data.application_name};{others}"
        except ValueError:
            stack = f"{stack};{enrich_data.application_name}"

    return f"{enrich_data.application_prefix}{enrich_data.container_prefix}{stack} {count}"


def concatenate_profiles(
    process_profiles: ProcessToStackSampleCounters,
    container_names_client: Optional[ContainerNamesClient],
    enrichment_options: EnrichmentOptions,
    metadata: Metadata,
    metrics: Metrics,
) -> Tuple[str, int]:
    """
    Concatenate all stacks from all stack mappings in process_profiles.
    Add "profile metadata" and metrics as the first line of the resulting collapsed file.
    """
    total_samples = 0
    lines = []
    # the metadata list always contains a "null" entry with index 0 - that's the index used for all
    # processes for which we didn't collect any metadata.
    application_metadata: List[Optional[Dict]] = [None]

    for pid, stacks in process_profiles.items():
        enrich_data = _enrich_pid_stacks(pid, enrichment_options, container_names_client, application_metadata)
        for stack, count in stacks.items():
            total_samples += count
            lines.append(_enrich_and_finalize_stack(stack, count, enrichment_options, enrich_data))

    lines.insert(
        0,
        _make_profile_metadata(
            container_names_client,
            enrichment_options.container_names,
            metadata,
            metrics,
            application_metadata,
            enrichment_options.application_metadata,
        ),
    )
    return "\n".join(lines), total_samples


def merge_profiles(
    perf_pid_to_stacks_counter: ProcessToStackSampleCounters,
    process_profiles: ProcessToStackSampleCounters,
    container_names_client: Optional[ContainerNamesClient],
    enrichment_options: EnrichmentOptions,
    metadata: Metadata,
    metrics: Metrics,
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
        scaled_stacks = scale_sample_counts(stacks, ratio)

        # swap them: use the samples from the runtime profiler.
        perf_pid_to_stacks_counter[pid] = scaled_stacks

    return concatenate_profiles(
        perf_pid_to_stacks_counter, container_names_client, enrichment_options, metadata, metrics
    )
