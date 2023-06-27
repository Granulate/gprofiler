#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from contextlib import _GeneratorContextManager
from pathlib import Path
from typing import Callable, List

import psutil
import pytest
from docker import DockerClient
from docker.models.images import Image
from granulate_utils.linux.process import is_musl

from gprofiler.profilers.python import PySpyProfiler
from gprofiler.profilers.python_ebpf import PythonEbpfProfiler
from tests.conftest import AssertInCollapsed
from tests.utils import (
    assert_function_in_collapsed,
    is_aarch64,
    is_pattern_in_collapsed,
    snapshot_pid_collapsed,
    snapshot_pid_profile,
    start_gprofiler_in_container_for_one_session,
    wait_for_log,
)


@pytest.fixture
def runtime() -> str:
    return "python"


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("application_image_tag", ["libpython"])
@pytest.mark.parametrize("profiler_type", ["pyspy"])
def test_python_select_by_libpython(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    profiler_flags: List[str],
    make_profiler_instance: Callable[[], _GeneratorContextManager],
) -> None:
    """
    Tests that profiling of processes running Python, whose basename(readlink("/proc/pid/exe")) isn't "python"
    (and also their comm isn't "python", for example, uwsgi).
    We expect to select these because they have "libpython" in their "/proc/pid/maps".
    This test runs a Python named "shmython".
    """
    profiler_flags.extend(["-f", "1000", "-d", "1"])
    with make_profiler_instance() as profiler:
        with profiler:
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
    profiler_flags: List[str],
    make_profiler_instance: Callable[[], _GeneratorContextManager],
) -> None:
    python_version, libc, app = application_image_tag.split("-")

    if python_version == "3.5" and profiler_type == "pyperf":
        pytest.skip("PyPerf doesn't support Python 3.5!")

    if python_version == "2.7" and profiler_type == "pyperf" and app == "uwsgi":
        pytest.xfail("This combination fails, see https://github.com/Granulate/gprofiler/issues/485")

    if is_aarch64():
        if profiler_type == "pyperf":
            pytest.skip(
                "PyPerf doesn't support aarch64 architecture, see https://github.com/Granulate/gprofiler/issues/499"
            )

        if python_version == "2.7" and profiler_type == "py-spy" and app == "uwsgi":
            pytest.xfail("This combination fails, see https://github.com/Granulate/gprofiler/issues/713")

        if python_version in ["3.7", "3.8", "3.9", "3.10", "3.11"] and profiler_type == "py-spy" and libc == "musl":
            pytest.xfail("This combination fails, see https://github.com/Granulate/gprofiler/issues/714")

    profiler_flags.extend(["-f", "1000", "-d", "2"])
    with make_profiler_instance() as profiler:
        with profiler:
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
    profiler_flags: List[str],
    make_profiler_instance: Callable[[], _GeneratorContextManager],
) -> None:
    if is_aarch64() and profiler_type == "pyperf":
        pytest.skip(
            "PyPerf doesn't support aarch64 architecture, see https://github.com/Granulate/gprofiler/issues/499"
        )

    profiler_flags.extend(["-f", "1000", "-d", "2"])
    with make_profiler_instance() as profiler:
        with profiler:
            profile = snapshot_pid_profile(profiler, application_pid)
    python_version, _, _ = application_image_tag.split("-")
    interpreter_frame = "PyEval_EvalFrameEx" if python_version == "2.7" else "_PyEval_EvalFrameDefault"
    collapsed = profile.stacks
    assert_collapsed(collapsed)
    assert_function_in_collapsed(interpreter_frame, collapsed)
    assert insert_dso_name == is_pattern_in_collapsed(
        rf"{interpreter_frame} \(.+?/libpython{python_version}.*?\.so.*?\)_\[pn\]", collapsed
    )


@pytest.mark.parametrize(
    "runtime,profiler_type,profiler_class_name",
    [
        ("python", "py-spy", PySpyProfiler.__name__),
        ("python", "pyperf", PythonEbpfProfiler.__name__),
        ("python", "auto", PythonEbpfProfiler.__name__),
        ("python", "enabled", PythonEbpfProfiler.__name__),
    ],
)
def test_select_specific_python_profiler(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    runtime_specific_args: List[str],
    profiler_flags: List[str],
    profiler_type: str,
    profiler_class_name: str,
) -> None:
    """
    Test that correct profiler class is selected as given by --python-mode argument.
    """
    if profiler_type == "pyperf" and is_aarch64():
        pytest.xfail("PyPerf doesn't run on Aarch64 - https://github.com/Granulate/gprofiler/issues/499")
    elif profiler_type == "enabled":
        # make sure the default behavior, with implicit enabled mode leads to auto selection
        profiler_flags.remove(f"--python-mode={profiler_type}")
    profiler_flags.extend(["--no-perf"])
    gprofiler = start_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, output_directory, output_collapsed, runtime_specific_args, profiler_flags
    )
    wait_for_log(gprofiler, "gProfiler initialized and ready to start profiling", 0, timeout=7)
    assert f"Initialized {profiler_class_name}".encode() in gprofiler.logs()
    gprofiler.remove(force=True)
