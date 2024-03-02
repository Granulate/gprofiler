#
# Copyright (C) 2022 Intel Corporation
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

"""
Tests for the logic from gprofiler/merge.py
"""

from typing import Dict

import pytest
from granulate_utils.metadata import Metadata

from gprofiler.gprofiler_types import ProcessToProfileData, ProcessToStackSampleCounters, ProfileData
from gprofiler.merge import merge_profiles
from gprofiler.metadata.enrichment import EnrichmentOptions
from gprofiler.system_metrics import Metrics
from gprofiler.utils.collapsed_format import parse_many_collapsed


def parse_profiles_text(profiles_text: str) -> ProcessToProfileData:
    parsed: ProcessToStackSampleCounters = parse_many_collapsed(profiles_text)
    process_to_profile_data: ProcessToProfileData = dict()
    for pid in parsed:
        process_to_profile_data[pid] = ProfileData(parsed[pid], None, None, None)
    return process_to_profile_data


@pytest.mark.parametrize(
    "input_dict, expected",
    [
        pytest.param(
            dict(
                perf_text="python-123/123;[unknown];_PyEval_EvalFrameDefault;(/usr/local/lib/libpython3.6m.so.1.0);"
                "(/usr/local/lib/libpython3.6m.so.1.0) 3",
                process_text="python-123/123;[Profiling error: exception CalledProcessError] 1",
            ),
            ";python;[Profiling error: exception CalledProcessError];[unknown];_PyEval_EvalFrameDefault;"
            "(/usr/local/lib/libpython3.6m.so.1.0);(/usr/local/lib/libpython3.6m.so.1.0) 3",
            id="1perf_1pyspy-error",
        ),
        pytest.param(
            dict(
                perf_text="python-123/123;_PyObject_Call_Prepend;_PyObject_FastCallDict;_PyFunction_FastCallDict 2\n"
                "python-123/123;_start;__libc_start_main;main;Py_Main;PyRun_SimpleFileExFlags 3\n"
                "python-123/123;entry_SYSCALL_64_[k] 1",
                process_text="python-123/123;[Profiling error: exception CalledProcessError] 1",
            ),
            ";python;[Profiling error: exception CalledProcessError];_PyObject_Call_Prepend;_PyObject_FastCallDict;"
            "_PyFunction_FastCallDict 2\n"
            ";python;[Profiling error: exception CalledProcessError];_start;__libc_start_main;main;Py_Main;"
            "PyRun_SimpleFileExFlags 3\n"
            ";python;[Profiling error: exception CalledProcessError];entry_SYSCALL_64_[k] 1",
            id="3perf_1pyspy-error",
        ),
        pytest.param(
            dict(
                perf_text="java-123/123;(/opt/java/lib/libjvm.so);__vdso_gettimeofday;(vdso);apic_timer_interrupt_[k];"
                "smp_apic_timer_interrupt_[k] 4",
                process_text="java-123/123;[Profiling skipped: async-profiler is already loaded] 1",
            ),
            ";java;[Profiling skipped: async-profiler is already loaded];(/opt/java/lib/libjvm.so);__vdso_gettimeofday;"
            "(vdso);apic_timer_interrupt_[k];smp_apic_timer_interrupt_[k] 4",
            id="1perf_1java-skipped",
        ),
        pytest.param(
            dict(
                perf_text="python-123/123;EMPTY 0",
                process_text="python-123/123;[Profiling error: exception CalledProcessError] 1",
            ),
            "",
            id="empty-perf_1pyspy-error",
        ),
        pytest.param(
            dict(
                perf_text="python-123/123;Py_Main;PyRun_SimpleFileExFlags 5\n"
                "java-456/456;[unknown];pthread_getname_np;do_syscall_64_[k] 3",
                process_text="python-123/123;[Profiling error: exception CalledProcessError] 1\n"
                "java-456/456;[Profiling skipped: profiled-oom] 1",
            ),
            ";python;[Profiling error: exception CalledProcessError];Py_Main;PyRun_SimpleFileExFlags 5\n"
            ";java;[Profiling skipped: profiled-oom];[unknown];pthread_getname_np;do_syscall_64_[k] 3",
            id="2pids_1perf-each_1java_1pyspy",
        ),
    ],
)
def test_merge_profiles_onto_errors(input_dict: Dict[str, str], expected: str) -> None:
    enrichment_options = EnrichmentOptions(
        profile_api_version=None,
        container_names=False,
        application_identifiers=False,
        application_identifier_args_filters=[],
        application_metadata=False,
    )
    metadata: Metadata = dict(profiling_mode="cpu")
    metrics = Metrics(cpu_avg=1.0, mem_avg=1.0)
    perf_pid_to_profiles = parse_profiles_text(input_dict["perf_text"])
    process_profiles = parse_profiles_text(input_dict["process_text"])
    header_outcome = merge_profiles(
        perf_pid_to_profiles=perf_pid_to_profiles,
        process_profiles=process_profiles,
        container_names_client=None,
        enrichment_options=enrichment_options,
        metadata=metadata,
        metrics=metrics,
        external_app_metadata={},
    ).split("\n", maxsplit=1)
    header, outcome = header_outcome[0], header_outcome[1] if len(header_outcome) == 2 else ""
    assert header.startswith("#")
    assert expected == outcome
