#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from pathlib import Path
from threading import Event
from typing import cast

import pytest
from docker.models.containers import Container

from gprofiler.profilers.perf import DEFAULT_PERF_DWARF_STACK_SIZE, SystemProfiler
from tests.utils import assert_function_in_collapsed, is_function_in_collapsed, snapshot_pid_collapsed


@pytest.fixture
def system_profiler(tmp_path: Path, perf_mode: str) -> SystemProfiler:
    return SystemProfiler(
        99,
        1,
        Event(),
        str(tmp_path),
        False,
        perf_mode=perf_mode,
        perf_inject=False,
        perf_dwarf_stack_size=DEFAULT_PERF_DWARF_STACK_SIZE,
    )


@pytest.mark.parametrize("runtime", ["native_fp", "native_dwarf"])
@pytest.mark.parametrize("perf_mode", ["fp", "dwarf", "smart"])
@pytest.mark.parametrize("in_container", [True])  # native app is built only for container
def test_perf_fp_dwarf_smart(
    system_profiler: SystemProfiler,
    application_pid: int,
    runtime: str,
    perf_mode: str,
) -> None:
    with system_profiler as profiler:
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
