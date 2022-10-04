#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

"""
Tests for the logic from gprofiler/merge.py
"""

from typing import Dict
import pytest

from gprofiler.merge import get_average_frame_count, _collapse_stack


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
        pytest.param("	 7f80 operator new+0x0 (/lib/libstdc++.so)", dict(
            dso_true="operator new (/lib/libstdc++.so)",
            dso_false="operator new",
        ), id="operator_new"),
        pytest.param("	 5501 [unknown] (/bin/cat)", dict(
            dso_true="(/bin/cat)",
            dso_false="(/bin/cat)",
        ), id="unknown_bin_cat"),
        pytest.param("	 7fd4 [unknown] (/lib/libudev.so (deleted))", dict(
            dso_true="(/lib/libudev.so (deleted))",
            dso_false="(/lib/libudev.so (deleted))",
        ), id="libudev_deleted"),
        pytest.param("	 1c00 [unknown] ([unknown])", dict(
            dso_true="[unknown] (unknown)",
            dso_false="[unknown]",
        ), id="uknown_unknown"),
        pytest.param("	 7fdb [unknown] ([vdso])", dict(
            dso_true="(vdso)",
            dso_false="(vdso)",
        ), id="unknown_vdso"),
        pytest.param("	 7f51 __gettime+0x1 ([vdso])", dict(
            dso_true="__gettime (vdso)",
            dso_false="__gettime",
        ), id="gettime_vdso"),
        pytest.param("	 ffa5 dup_mm+0x3f5 ([kernel.kallsyms])", dict(
            dso_true="dup_mm_[k]",
            dso_false="dup_mm_[k]",
        ), id="dup_mm_kernel"),
        pytest.param("	 dbdb CancelableTask::Run()+0x3b (/root/node)", dict(
            dso_true="CancelableTask::Run() (/root/node)",
            dso_false="CancelableTask::Run()",
        ), id="cancelable_task_run_node"),
        pytest.param("	 4090 recursive+0x1e (/native)", dict(
            dso_true="recursive (/native)",
            dso_false="recursive",
        ), id="recursive_native"),
        pytest.param("	 7fa0 @plt+0x0 (/lib/libc.so)", dict(
            dso_true="@plt (/lib/libc.so)",
            dso_false="@plt",
        ), id="plt_libc"),
    ],
)
def test_collapse_stack_consider_dso(stack: str, insert_dso_name: bool, outcome_dict: Dict[str, str]) -> None:
    expected = f"program;{outcome_dict['dso_true' if insert_dso_name else 'dso_false']}"
    assert expected == _collapse_stack("program", stack, insert_dso_name)
