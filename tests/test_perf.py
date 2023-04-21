#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import logging
from threading import Event
from typing import Dict, cast

import pytest
from docker.models.containers import Container
from pytest import LogCaptureFixture

from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.perf import (
    DEFAULT_PERF_DWARF_STACK_SIZE,
    SystemProfiler,
    _collapse_stack,
    get_average_frame_count,
)
from gprofiler.utils import wait_event
from tests.utils import (
    assert_function_in_collapsed,
    is_aarch64,
    is_function_in_collapsed,
    snapshot_pid_collapsed,
    snapshot_pid_profile,
)


@pytest.fixture
def system_profiler(perf_mode: str, insert_dso_name: bool, profiler_state: ProfilerState) -> SystemProfiler:
    return make_system_profiler(perf_mode, profiler_state)


def make_system_profiler(perf_mode: str, profiler_state: ProfilerState) -> SystemProfiler:
    return SystemProfiler(
        99,
        1,
        profiler_state,
        perf_mode=perf_mode,
        perf_inject=False,
        perf_dwarf_stack_size=DEFAULT_PERF_DWARF_STACK_SIZE,
        perf_node_attach=False,
        perf_memory_restart=True,
    )


@pytest.mark.parametrize("runtime", ["native_fp", "native_dwarf"])
@pytest.mark.parametrize("perf_mode", ["fp", "dwarf", "smart"])
@pytest.mark.parametrize("in_container", [True])  # native app is built only for container
def test_perf_fp_dwarf_smart(
    system_profiler: SystemProfiler,
    application_pid: int,
    runtime: str,
    perf_mode: str,
    application_docker_container: Container,
) -> None:
    if is_aarch64():
        if runtime == "native_fp" and perf_mode == "fp":
            pytest.xfail("This combination fails on aarch64 https://github.com/Granulate/gprofiler/issues/746")
        if runtime == "native_fp" and perf_mode == "dwarf":
            pytest.xfail("This combination fails on aarch64 https://github.com/Granulate/gprofiler/issues/746")
        if runtime == "native_dwarf" and perf_mode == "smart":
            pytest.xfail("This combination fails on aarch64 https://github.com/Granulate/gprofiler/issues/746")
        if runtime == "native_dwarf" and perf_mode == "dwarf":
            pytest.xfail("This combination fails on aarch64 https://github.com/Granulate/gprofiler/issues/746")
    with system_profiler as profiler:
        process_profile = snapshot_pid_profile(profiler, application_pid)
        process_collapsed = process_profile.stacks

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
        # Check if container name is added to ProfileData
        assert application_docker_container.name == process_profile.container_name


def _restart_app_container(application_docker_container: Container) -> int:
    application_docker_container.restart(timeout=0)
    application_docker_container.reload()  # post restart
    return cast(int, application_docker_container.attrs["State"]["Pid"])


def _assert_comm_in_profile(profiler: SystemProfiler, application_pid: int, exec_comm: bool) -> None:
    collapsed = snapshot_pid_collapsed(profiler, application_pid)
    # native is the original comm
    # oative is the changed comm of the main thread
    # pative is the changed comm of other threads
    if exec_comm:
        assert is_function_in_collapsed("native;", collapsed)
        assert not is_function_in_collapsed("oative;", collapsed)
        assert not is_function_in_collapsed("pative;", collapsed)
    else:
        assert not is_function_in_collapsed("native;", collapsed)
        assert is_function_in_collapsed("oative;", collapsed)
        assert not is_function_in_collapsed("pative;", collapsed)


@pytest.mark.parametrize("runtime", ["native_change_comm"])
@pytest.mark.parametrize("perf_mode", ["fp"])  # only fp is enough
@pytest.mark.parametrize("in_container", [True])  # native app is built only for container
def test_perf_comm_change(
    system_profiler: SystemProfiler,
    application_pid: int,
    application_docker_container: Container,
    perf_mode: str,
) -> None:
    """
    Runs a program that changes its comm (i.e comm of the main thread).
    The stack output by perf should use the original name, not the changed one.

    This tests our modification to perf here:
    https://github.com/Granulate/linux/commit/40a7823cf90a7e69ce8af88d224dfdd7e371de2d

    perf records "comm" events - changes of task comms. Samples printed for a thread will use the current comm
    perf understands that it has. Our modification makes it select a PERF_RECORD_COMM event that has the "exec"
    flag. Below are 2 events from the native program - the first is the name it was executed with, then
    the rename. If perf has started before the app, we expect to see the exec name and not the current name.

        native 1901079 [010] 984807.648525: PERF_RECORD_COMM exec: native:1901079/1901079
        oative 1901079 [010] 984807.648792: PERF_RECORD_COMM: oative:1901079/1901079

    If perf started after the app, we will see the changed name (although I'd prefer to see the original name, but
    I'm not sure it can be done, i.e is this info even kept anywhere).
    """
    with system_profiler as profiler:
        # first run - we get the changed name, because the app started before perf began recording.
        _assert_comm_in_profile(profiler, application_pid, False)

        # now, while perf is still running & recording, we restart the app
        application_pid = _restart_app_container(application_docker_container)

        # second run - we get the original name, because the app started after perf began recording.
        _assert_comm_in_profile(profiler, application_pid, True)


@pytest.mark.parametrize("runtime", ["native_thread_comm"])
@pytest.mark.parametrize("perf_mode", ["fp"])  # only fp is enough
@pytest.mark.parametrize("in_container", [True])  # native app is built only for container
def test_perf_thread_comm_is_process_comm(
    system_profiler: SystemProfiler,
    application_pid: int,
    application_docker_container: Container,
    perf_mode: str,
) -> None:
    """
    Runs a program that changes its comm (i.e comm of the main thread), then starts a thread that
    changes its comm.
    The stack output by perf should use the comm of the process (i.e the main thread), and when the process
    starts after perf, the exec comm of the process should be used (see test_perf_comm_change)
    """
    with system_profiler as profiler:
        # running perf & script now with --show-task-events would show:
        #   pative 1925947 [010] 987095.272656: PERF_RECORD_COMM: pative:1925904/1925947
        # our perf will prefer to use the exec comm, OR oldest comm available if exec
        # one is not present; see logic in thread__exec_comm().

        # first run - we get the changed name, because the app started before perf began recording.
        # note that we still pick the process name and not thread name! (thread native is 'pative')
        _assert_comm_in_profile(profiler, application_pid, False)

        # now, while perf is still running & recording, we restart the app
        application_pid = _restart_app_container(application_docker_container)

        # for clarity, these are the COMM events that happen here:
        #   native 1925904 [006] 987095.271786: PERF_RECORD_COMM exec: native:1925904/1925904
        #   oative 1925904 [006] 987095.272430: PERF_RECORD_COMM: oative:1925904/1925904
        #   oative 1925904 [006] 987095.272545: PERF_RECORD_FORK(1925904:1925947):(1925904:1925904)
        #   pative 1925947 [010] 987095.272656: PERF_RECORD_COMM: pative:1925904/1925947
        # we take the exec comm for all threads so we remain with the first, "native".
        _assert_comm_in_profile(profiler, application_pid, True)


@pytest.mark.parametrize("runtime", ["native_thread_comm"])
@pytest.mark.parametrize("perf_mode", ["fp"])
@pytest.mark.parametrize("insert_dso_name", [False, True])
@pytest.mark.parametrize("in_container", [True])
def test_dso_name_in_perf_profile(
    system_profiler: SystemProfiler,
    application_pid: int,
    insert_dso_name: bool,
) -> None:
    with system_profiler as profiler:
        collapsed = snapshot_pid_profile(profiler, application_pid).stacks
        assert is_function_in_collapsed("recursive", collapsed)
        assert insert_dso_name == is_function_in_collapsed("recursive (/native)", collapsed)


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("perf_mode", ["smart"])
def test_perf_restarted_if_killed(
    system_profiler: SystemProfiler,
    caplog: LogCaptureFixture,
    in_container: bool,
) -> None:
    caplog.set_level(logging.DEBUG)
    with system_profiler as profiler:
        # both perfs started
        assert len(profiler._perfs) == 2
        assert len(list(filter(lambda r: r.message == "Starting perf (fp mode)", caplog.records))) == 1
        assert len(list(filter(lambda r: r.message == "Starting perf (dwarf mode)", caplog.records))) == 1

        # kill both
        for perf in profiler._perfs:
            assert perf._process is not None
            perf._process.terminate()

        # wait for them to exit
        wait_event(20, Event(), lambda: all(not perf.is_running() for perf in profiler._perfs))

        # snapshot - perfs are restarted after a cycle
        profiler.snapshot()

        # they should restart
        assert all(perf.is_running() for perf in profiler._perfs)
        assert (
            len(
                list(
                    filter(
                        lambda r: r.message == "perf (fp mode) not running (unexpectedly), restarting...",
                        caplog.records,
                    )
                )
            )
            == 1
        )
        assert (
            len(
                list(
                    filter(
                        lambda r: r.message == "perf (dwarf mode) not running (unexpectedly), restarting...",
                        caplog.records,
                    )
                )
            )
            == 1
        )
        # starting message again (now appears twice)
        assert len(list(filter(lambda r: r.message == "Starting perf (fp mode)", caplog.records))) == 2
        assert len(list(filter(lambda r: r.message == "Starting perf (dwarf mode)", caplog.records))) == 2


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
