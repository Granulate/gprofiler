#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

"""
Tests for the logic from gprofiler/merge.py
"""

from typing import Dict

import pytest
from granulate_utils.metadata import Metadata

from gprofiler.gprofiler_types import ProcessToProfileData, ProcessToStackSampleCounters, ProfileData
from gprofiler.merge import _collapse_stack, get_average_frame_count, merge_profiles, parse_many_collapsed
from gprofiler.metadata.enrichment import EnrichmentOptions
from gprofiler.system_metrics import Metrics


@pytest.mark.parametrize(
    "samples,count",
    [
        (["a 1"], 1),
        (["d_[k] 1"], 0),
        (["d_[k];e_[k] 1"], 0),
        (["a;b;c;d_[k] 1"], 3),
        (["a;b;c;d_[k];e_[k] 1"], 3),
        (["a 1", "a;b 1"], 1.5),
        (["d_[k] 1", "a;d_[k] 1"], 0.5),
    ],
)
def test_get_average_frame_count(samples: str, count: float) -> None:
    assert get_average_frame_count(samples) == count


@pytest.mark.parametrize("insert_dso_name", [False, True])
@pytest.mark.parametrize(
    "stack, outcome_dict",
    [
        pytest.param(
            "	 7f80 operator new+0x0 (/lib/libstdc++.so)",
            dict(
                dso_true="operator new (/lib/libstdc++.so)",
                dso_false="operator new",
            ),
            id="operator_new",
        ),
        pytest.param(
            "	 5501 [unknown] (/bin/cat)",
            dict(
                dso_true="(/bin/cat)",
                dso_false="(/bin/cat)",
            ),
            id="unknown_bin_cat",
        ),
        pytest.param(
            "	 7fd4 [unknown] (/lib/libudev.so (deleted))",
            dict(
                dso_true="(/lib/libudev.so (deleted))",
                dso_false="(/lib/libudev.so (deleted))",
            ),
            id="libudev_deleted",
        ),
        pytest.param(
            "	 1c00 [unknown] ([unknown])",
            dict(
                dso_true="[unknown] (unknown)",
                dso_false="[unknown]",
            ),
            id="uknown_unknown",
        ),
        pytest.param(
            "	 7fdb [unknown] ([vdso])",
            dict(
                dso_true="(vdso)",
                dso_false="(vdso)",
            ),
            id="unknown_vdso",
        ),
        pytest.param(
            "	 7f51 __gettime+0x1 ([vdso])",
            dict(
                dso_true="__gettime (vdso)",
                dso_false="__gettime",
            ),
            id="gettime_vdso",
        ),
        pytest.param(
            "	 ffa5 dup_mm+0x3f5 ([kernel.kallsyms])",
            dict(
                dso_true="dup_mm_[k]",
                dso_false="dup_mm_[k]",
            ),
            id="dup_mm_kernel",
        ),
        pytest.param(
            "	 dbdb CancelableTask::Run()+0x3b (/root/node)",
            dict(
                dso_true="CancelableTask::Run() (/root/node)",
                dso_false="CancelableTask::Run()",
            ),
            id="cancelable_task_run_node",
        ),
        pytest.param(
            "	 4090 recursive+0x1e (/native)",
            dict(
                dso_true="recursive (/native)",
                dso_false="recursive",
            ),
            id="recursive_native",
        ),
        pytest.param(
            "	 7fa0 @plt+0x0 (/lib/libc.so)",
            dict(
                dso_true="@plt (/lib/libc.so)",
                dso_false="@plt",
            ),
            id="plt_libc",
        ),
        pytest.param(
            "	ff84 page_fault+0x34 ([kernel.kallsyms])\n"
            "	7f6e [unknown] (/tmp/perf-123.map)\n"
            "	fffc strncpy_from_user+0x4c ([kernel.kallsyms])\n"
            "	7fae fstatat64+0xe (/lib/libc-2.33.so)\n"
            "	0040 [unknown] ([unknown])",
            dict(
                dso_true="[unknown] (unknown);fstatat64 (/lib/libc-2.33.so);"
                "strncpy_from_user_[k];(/tmp/perf-123.map);page_fault_[k]",
                dso_false="[unknown];fstatat64;strncpy_from_user_[k];(/tmp/perf-123.map);page_fault_[k]",
            ),
            id="mixed_stack",
        ),
        pytest.param(
            "	b7ac [unknown] ([unknown])\n"
            "	7fae Resolver::_invokedynamic+0xbe (/opt/java/lib/libjvm.so)\n"
            "	7f2f [unknown] (/tmp/perf-123.map)\n"
            "	7f8e JavaMain+0xcfe (/opt/java/lib/libjli.so)\n"
            "	7fdb start_thread+0xdb (/lib/libpthread-2.27.so)\n",
            dict(
                dso_true="start_thread (/lib/libpthread-2.27.so);JavaMain (/opt/java/lib/libjli.so);"
                "(/tmp/perf-123.map);Resolver::_invokedynamic (/opt/java/lib/libjvm.so);[unknown] (unknown)",
                dso_false="start_thread;JavaMain;(/tmp/perf-123.map);Resolver::_invokedynamic;[unknown]",
            ),
            id="mixed_java_stack",
        ),
    ],
)
def test_collapse_stack_consider_dso(stack: str, insert_dso_name: bool, outcome_dict: Dict[str, str]) -> None:
    expected = f"program;{outcome_dict['dso_true' if insert_dso_name else 'dso_false']}"
    assert expected == _collapse_stack("program", stack, insert_dso_name)


def parse_profiles_text(profiles_text: str) -> ProcessToProfileData:
    parsed: ProcessToStackSampleCounters = parse_many_collapsed(profiles_text)
    process_to_profile_data: ProcessToProfileData = dict()
    for pid in parsed:
        process_to_profile_data[pid] = ProfileData(parsed[pid], None, None)
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
        perf_pid_to_profiles, process_profiles, None, enrichment_options, metadata, metrics
    ).split("\n", maxsplit=1)
    header, outcome = header_outcome[0], header_outcome[1] if len(header_outcome) == 2 else ""
    assert header.startswith("#")
    assert expected == outcome
