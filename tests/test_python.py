#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os

import psutil
import pytest
from granulate_utils.linux.process import is_musl

from gprofiler.profiler_state import ProfilerState
from gprofiler.profilers.python import PythonProfiler
from tests.conftest import AssertInCollapsed
from tests.utils import (
    assert_function_in_collapsed,
    is_aarch64,
    is_pattern_in_collapsed,
    snapshot_pid_collapsed,
    snapshot_pid_profile,
)


@pytest.fixture
def runtime() -> str:
    return "python"


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("application_image_tag", ["libpython"])
def test_python_select_by_libpython(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    profiler_state: ProfilerState,
) -> None:
    """
    Tests that profiling of processes running Python, whose basename(readlink("/proc/pid/exe")) isn't "python"
    (and also their comm isn't "python", for example, uwsgi).
    We expect to select these because they have "libpython" in their "/proc/pid/maps".
    This test runs a Python named "shmython".
    """
    with PythonProfiler(1000, 1, profiler_state, "pyspy", True, None, False) as profiler:
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
    assert_collapsed(process_collapsed)
    assert all(stack.startswith("shmython") for stack in process_collapsed.keys())


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize(
    "application_image_tag",
    [
        "2.7-glibc-python",
        "2.7-musl-python",
        "3.5-glibc-python",
        "3.5-musl-python",
        "3.6-glibc-python",
        "3.6-musl-python",
        "3.7-glibc-python",
        "3.7-musl-python",
        "3.8-glibc-python",
        "3.8-musl-python",
        "3.9-glibc-python",
        "3.9-musl-python",
        "3.10-glibc-python",
        "3.10-musl-python",
        "3.11-glibc-python",
        "3.11-musl-python",
        "2.7-glibc-uwsgi",
        "2.7-musl-uwsgi",
        "3.7-glibc-uwsgi",
        "3.7-musl-uwsgi",
    ],
)
@pytest.mark.parametrize("profiler_type", ["py-spy", "pyperf"])
def test_python_matrix(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    profiler_type: str,
    application_image_tag: str,
    profiler_state: ProfilerState,
) -> None:
    python_version, libc, app = application_image_tag.split("-")

    if python_version == "3.5" and profiler_type == "pyperf":
        pytest.skip("PyPerf doesn't support Python 3.5!")

    if python_version == "2.7" and profiler_type == "pyperf" and app == "uwsgi":
        pytest.xfail("This combination fails, see https://github.com/Granulate/gprofiler/issues/485")

    if python_version == "3.11" and profiler_type == "pyperf":
        pytest.xfail("PyPerf does not support 3.11 - https://github.com/Granulate/gprofiler/issues/727")

    if is_aarch64():
        if profiler_type == "pyperf":
            pytest.skip(
                "PyPerf doesn't support aarch64 architecture, see https://github.com/Granulate/gprofiler/issues/499"
            )

        if python_version == "2.7" and profiler_type == "py-spy" and app == "uwsgi":
            pytest.xfail("This combination fails, see https://github.com/Granulate/gprofiler/issues/713")

        if python_version in ["3.7", "3.8", "3.9", "3.10", "3.11"] and profiler_type == "py-spy" and libc == "musl":
            pytest.xfail("This combination fails, see https://github.com/Granulate/gprofiler/issues/714")

    with PythonProfiler(1000, 2, profiler_state, profiler_type, True, None, False) as profiler:
        profile = snapshot_pid_profile(profiler, application_pid)

    collapsed = profile.stacks

    assert_collapsed(collapsed)
    # searching for "python_version.", because ours is without the patchlevel.
    assert_function_in_collapsed(f"standard-library=={python_version}.", collapsed)

    assert libc in ("musl", "glibc")
    assert (libc == "musl") == is_musl(psutil.Process(application_pid))

    if profiler_type == "pyperf":
        # we expect to see kernel code
        assert_function_in_collapsed("do_syscall_64_[k]", collapsed)
        # and native user code
        assert_function_in_collapsed(
            "PyEval_EvalFrameEx_[pn]" if python_version == "2.7" else "_PyEval_EvalFrameDefault_[pn]", collapsed
        )
        # ensure class name exists for instance methods
        assert_function_in_collapsed("lister.Burner.burner", collapsed)
        # ensure class name exists for class methods
        assert_function_in_collapsed("lister.Lister.lister", collapsed)

    assert profile.app_metadata is not None
    assert os.path.basename(profile.app_metadata["execfn"]) == app
    # searching for "python_version.", because ours is without the patchlevel.
    assert profile.app_metadata["python_version"].startswith(f"Python {python_version}.")
    if python_version == "2.7" and app == "python":
        assert profile.app_metadata["sys_maxunicode"] == "1114111"
    else:
        assert profile.app_metadata["sys_maxunicode"] is None


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("profiler_type", ["pyperf"])
@pytest.mark.parametrize("insert_dso_name", [False, True])
@pytest.mark.parametrize(
    "application_image_tag",
    [
        "2.7-glibc-python",
        "3.10-glibc-python",
    ],
)
def test_dso_name_in_pyperf_profile(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    profiler_type: str,
    application_image_tag: str,
    insert_dso_name: bool,
    profiler_state: ProfilerState,
) -> None:
    if is_aarch64() and profiler_type == "pyperf":
        pytest.skip(
            "PyPerf doesn't support aarch64 architecture, see https://github.com/Granulate/gprofiler/issues/499"
        )

    with PythonProfiler(1000, 2, profiler_state, profiler_type, True, None, False) as profiler:
        profile = snapshot_pid_profile(profiler, application_pid)
    python_version, _, _ = application_image_tag.split("-")
    interpreter_frame = "PyEval_EvalFrameEx" if python_version == "2.7" else "_PyEval_EvalFrameDefault"
    collapsed = profile.stacks
    assert_collapsed(collapsed)
    assert_function_in_collapsed(interpreter_frame, collapsed)
    assert insert_dso_name == is_pattern_in_collapsed(
        rf"{interpreter_frame} \(.+?/libpython{python_version}.*?\.so.*?\)_\[pn\]", collapsed
    )
