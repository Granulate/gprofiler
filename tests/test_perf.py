#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from pathlib import Path
from threading import Event

import pytest

from gprofiler.profilers.perf import DEFAULT_PERF_DWARF_STACK_SIZE, SystemProfiler
from tests.utils import assert_function_in_collapsed, is_function_in_collapsed, snapshot_pid_collapsed


@pytest.mark.parametrize("runtime", ["native_fp", "native_dwarf"])
@pytest.mark.parametrize("perf_mode", ["fp", "dwarf", "smart"])
@pytest.mark.parametrize("in_container", [True])  # tests depend on the version we use in the image
def test_perf(
    tmp_path: Path,
    application_pid: int,
    runtime: str,
    perf_mode: str,
) -> None:
    """ """
    with SystemProfiler(
        99,
        3,
        Event(),
        str(tmp_path),
        False,
        perf_mode=perf_mode,
        perf_inject=False,
        perf_dwarf_stack_size=DEFAULT_PERF_DWARF_STACK_SIZE,
    ) as profiler:
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)

        if runtime == "native_dwarf":
            # app is built with DWARF info and without FP, so we expect to see a callstack only in DWARF or smart modes.
            assert is_function_in_collapsed(";recursive;recursive;recursive;recursive;", process_collapsed) ^ bool(
                perf_mode not in ("dwarf", "smart")
            )
        else:
            # app is built with FP and without DWARF info, but DWARF mode is able to do FP unwinding,
            # so it should always succeed.
            assert runtime == "native_fp"
            assert_function_in_collapsed(";recursive;recursive;recursive;recursive;", process_collapsed)

        # expect to see libc stacks only when collecting with DWARF or smart.
        assert is_function_in_collapsed(";_start;__libc_start_main;main;", process_collapsed) ^ bool(
            perf_mode not in ("dwarf", "smart")
        )
