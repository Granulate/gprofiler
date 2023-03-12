#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import json
import math
import random
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from granulate_utils.metadata import Metadata

from gprofiler.containers_client import ContainerNamesClient
from gprofiler.gprofiler_types import ProcessToProfileData, ProfileData, ProfilingErrorStack, StackToSampleCount
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.enrichment import EnrichmentOptions
from gprofiler.system_metrics import Metrics
from gprofiler.utils import merge_dicts

logger = get_logger_adapter(__name__)


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
        "profiling_mode": metadata["profiling_mode"],
    }
    return "# " + json.dumps(profile_metadata)


@dataclass
class PidStackEnrichment:
    appid: Optional[str]
    application_prefix: str
    container_prefix: str


def _get_container_name_from_profile(profile: ProfileData) -> str:
    if profile.container_name is not None:
        container_prefix = profile.container_name
    else:
        container_prefix = ""
    return f"{container_prefix};"


def _enrich_pid_stacks(
    profile: ProfileData,
    enrichment_options: EnrichmentOptions,
    application_metadata: List[Optional[Dict]],
) -> PidStackEnrichment:
    """
    Enrichment per app (or, PID here). This includes:
    * appid (or app name)
    * app metadata
    * container name
    """
    # generate application name
    appid = profile.appid
    if appid is not None:
        appid = f"appid: {appid}"

    # generate metadata
    app_metadata = profile.app_metadata
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
        container_prefix = _get_container_name_from_profile(profile)
    else:
        container_prefix = ""

    return PidStackEnrichment(appid, application_prefix, container_prefix)


def _enrich_and_finalize_stack(
    stack: str, count: int, enrichment_options: EnrichmentOptions, enrich_data: PidStackEnrichment
) -> str:
    """
    Attach the enrichment data collected for the PID of this stack.
    """
    if enrichment_options.application_identifiers and enrich_data.appid is not None:
        # insert the app name between the first frame and all others
        try:
            first_frame, others = stack.split(";", maxsplit=1)
            stack = f"{first_frame};{enrich_data.appid};{others}"
        except ValueError:
            stack = f"{stack};{enrich_data.appid}"

    return f"{enrich_data.application_prefix}{enrich_data.container_prefix}{stack} {count}"


def concatenate_from_external_file(
    collapsed_file_path: str,
    obtained_metadata: Metadata,
) -> Tuple[Optional[Any], Optional[Any], str]:
    """
    Concatenate all stacks from all stack mappings in process_profiles.
    Add "profile metadata" and metrics as the first line of the resulting collapsed file.
    """

    lines = []
    start_time = None
    end_time = None

    # TODO: container names and application metadata
    with open(collapsed_file_path) as file:
        for index, line in enumerate(file):
            if index == 0:
                assert line.startswith("#")
                read_metadata = json.loads(line[1:])
                metadata = merge_dicts(read_metadata, obtained_metadata)
                try:
                    start_time = datetime.fromisoformat(metadata["start_time"])
                    end_time = datetime.fromisoformat(metadata["end_time"])
                except KeyError:
                    pass
                try:
                    del metadata["run_arguments"]["func"]
                except KeyError:
                    pass
                lines.append("# " + json.dumps(metadata))
            else:
                lines.append(line.rstrip())

    return start_time, end_time, "\n".join(lines)


def concatenate_profiles(
    process_profiles: ProcessToProfileData,
    container_names_client: Optional[ContainerNamesClient],
    enrichment_options: EnrichmentOptions,
    metadata: Metadata,
    metrics: Metrics,
) -> str:
    """
    Concatenate all stacks from all stack mappings in process_profiles.
    Add "profile metadata" and metrics as the first line of the resulting collapsed file.
    """
    lines = []
    # the metadata list always contains a "null" entry with index 0 - that's the index used for all
    # processes for which we didn't collect any metadata.
    application_metadata: List[Optional[Dict]] = [None]

    for pid, profile in process_profiles.items():
        enrich_data = _enrich_pid_stacks(profile, enrichment_options, application_metadata)
        for stack, count in profile.stacks.items():
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
    return "\n".join(lines)


def merge_profiles(
    perf_pid_to_profiles: ProcessToProfileData,
    process_profiles: ProcessToProfileData,
    container_names_client: Optional[ContainerNamesClient],
    enrichment_options: EnrichmentOptions,
    metadata: Metadata,
    metrics: Metrics,
) -> str:
    # merge process profiles into the global perf results.
    for pid, profile in process_profiles.items():
        if len(profile.stacks) == 0:
            # no samples collected by the runtime profiler for this process (empty stackcollapse file)
            continue

        process_perf = perf_pid_to_profiles.get(pid)
        if process_perf is None:
            # no samples collected by perf for this process, so those collected by the runtime profiler
            # are dropped.
            perf_samples_count = 0
        else:
            perf_samples_count = sum(process_perf.stacks.values())

        profile_samples_count = sum(profile.stacks.values())
        assert profile_samples_count > 0

        if process_perf is not None and perf_samples_count > 0 and ProfilingErrorStack.is_error_stack(profile.stacks):
            # runtime profiler returned an error stack; extend it with perf profiler stacks for the pid
            profile.stacks = ProfilingErrorStack.attach_error_to_stacks(process_perf.stacks, profile.stacks)
        else:
            # do the scaling by the ratio of samples: samples we received from perf for this process,
            # divided by samples we received from the runtime profiler of this process.
            ratio = perf_samples_count / profile_samples_count
            profile.stacks = scale_sample_counts(profile.stacks, ratio)

        if process_perf is not None:
            if profile.container_name in [None, ""]:
                if process_perf.container_name is not None:
                    profile.container_name = process_perf.container_name

        # swap them: use the processed (scaled or extended) samples from the runtime profiler.
        perf_pid_to_profiles[pid] = profile

    return concatenate_profiles(perf_pid_to_profiles, container_names_client, enrichment_options, metadata, metrics)
