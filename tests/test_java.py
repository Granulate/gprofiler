#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import os
import shutil
import signal
import threading
import time
from collections import Counter
from pathlib import Path
from subprocess import Popen
from threading import Event
from typing import Any, Optional

import docker
import psutil
import pytest
from docker.models.containers import Container
from granulate_utils.linux.elf import get_elf_buildid
from packaging.version import Version
from pytest import LogCaptureFixture, MonkeyPatch

from gprofiler.profilers.java import (
    AsyncProfiledProcess,
    JavaProfiler,
    frequency_to_ap_interval,
    get_java_version,
    parse_jvm_version,
)
from gprofiler.utils import remove_prefix
from gprofiler.utils.process import is_musl
from tests.conftest import AssertInCollapsed
from tests.type_utils import cast_away_optional
from tests.utils import assert_function_in_collapsed, make_java_profiler, snapshot_one_collaped


def get_lib_path(application_pid: int, path: str) -> str:
    libs = set()
    for m in psutil.Process(application_pid).memory_maps():
        if path in m.path:
            libs.add(m.path)
    assert len(libs) == 1, f"found {libs!r} - expected 1"
    return f"/proc/{application_pid}/root/{libs.pop()}"


def get_libjvm_path(application_pid: int) -> str:
    return get_lib_path(application_pid, "/libjvm.so")


def is_libjvm_deleted(application_pid: int) -> bool:
    # can't use get_libjvm_path() - psutil removes "deleted" if the file actually exists...
    return "/libjvm.so (deleted)" in Path(f"/proc/{application_pid}/maps").read_text()


# adds the "status" command to AsyncProfiledProcess from gProfiler.
class AsyncProfiledProcessForTests(AsyncProfiledProcess):
    def status_async_profiler(self) -> None:
        self._run_async_profiler(
            self._get_base_cmd() + [f"status,log={self._log_path_process},file={self._output_path_process}"],
        )


@pytest.fixture
def runtime() -> str:
    return "java"


def test_async_profiler_already_running(
    application_pid: int, assert_collapsed: AssertInCollapsed, tmp_path: Path, caplog: LogCaptureFixture
) -> None:
    """
    Test we're able to restart async-profiler in case it's already running in the process and get results normally.
    """
    caplog.set_level(logging.INFO)
    with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
        process = profiler._select_processes_to_profile()[0]

        with AsyncProfiledProcess(
            process=process,
            storage_dir=profiler._storage_dir,
            stop_event=profiler._stop_event,
            buildids=False,
            mode=profiler._mode,
            ap_safemode=0,
            ap_args="",
        ) as ap_proc:
            assert ap_proc.start_async_profiler(frequency_to_ap_interval(11))
        assert any("libasyncProfiler.so" in m.path for m in process.memory_maps())
        # run "status"
        with AsyncProfiledProcessForTests(
            process=process,
            storage_dir=profiler._storage_dir,
            stop_event=profiler._stop_event,
            buildids=False,
            mode="itimer",
            ap_safemode=0,
            ap_args="",
        ) as ap_proc:
            ap_proc.status_async_profiler()
            # printed the output file, see ACTION_STATUS case in async-profiler/profiler.cpp
            assert "Profiling is running for " in cast_away_optional(ap_proc.read_output())

        # then start again
        collapsed = snapshot_one_collaped(profiler)
        assert "Found async-profiler already started" in caplog.text
        assert "Finished profiling process" in caplog.text
        assert_collapsed(collapsed)


@pytest.mark.parametrize("in_container", [True])
def test_java_async_profiler_cpu_mode(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
) -> None:
    """
    Run Java in a container and enable async-profiler in CPU mode, make sure we get kernel stacks.
    """
    with make_java_profiler(
        storage_dir=str(tmp_path),
        frequency=999,
        # this ensures auto selection picks CPU by default, if possible.
        java_async_profiler_mode="auto",
    ) as profiler:
        process_collapsed = snapshot_one_collaped(profiler)
        assert_collapsed(process_collapsed)
        assert_function_in_collapsed("do_syscall_64_[k]", process_collapsed)  # ensure kernels stacks exist


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("image_suffix", ["_musl"])
def test_java_async_profiler_musl_and_cpu(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
) -> None:
    """
    Run Java in an Alpine-based container and enable async-profiler in CPU mode, make sure that musl profiling
    works and that we get kernel stacks.
    """
    with make_java_profiler(storage_dir=str(tmp_path), frequency=999) as profiler:
        assert is_musl(psutil.Process(application_pid))

        process_collapsed = snapshot_one_collaped(profiler)
        assert_collapsed(process_collapsed)
        assert_function_in_collapsed("do_syscall_64_[k]", process_collapsed)  # ensure kernels stacks exist


def test_java_safemode_parameters(tmp_path: Path) -> None:
    with pytest.raises(AssertionError) as excinfo:
        make_java_profiler(storage_dir=str(tmp_path), java_version_check=False)
    assert "Java version checks are mandatory in --java-safemode" in str(excinfo.value)


def test_java_safemode_version_check(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
    application_docker_container: Container,
    application_process: Optional[Popen],
) -> None:
    monkeypatch.setitem(JavaProfiler.MINIMAL_SUPPORTED_VERSIONS, 8, (Version("8.999"), 0))

    with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
        process = profiler._select_processes_to_profile()[0]
        jvm_version = parse_jvm_version(get_java_version(process, profiler._stop_event))
        collapsed = snapshot_one_collaped(profiler)
        assert collapsed == Counter({"java;[Profiling skipped: profiling this JVM is not supported]": 1})

    log_record = next(filter(lambda r: r.message == "Unsupported JVM version", caplog.records))
    log_extra = log_record.gprofiler_adapter_extra  # type: ignore
    assert log_extra["jvm_version"] == repr(jvm_version)


def test_java_safemode_build_number_check(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
    application_docker_container: Container,
    application_process: Optional[Popen],
) -> None:
    with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
        process = profiler._select_processes_to_profile()[0]
        jvm_version = parse_jvm_version(get_java_version(process, profiler._stop_event))
        monkeypatch.setitem(JavaProfiler.MINIMAL_SUPPORTED_VERSIONS, 8, (jvm_version.version, 999))
        collapsed = snapshot_one_collaped(profiler)
        assert collapsed == Counter({"java;[Profiling skipped: profiling this JVM is not supported]": 1})

    log_record = next(filter(lambda r: r.message == "Unsupported JVM version", caplog.records))
    log_extra = log_record.gprofiler_adapter_extra  # type: ignore
    assert log_extra["jvm_version"] == repr(jvm_version)


@pytest.mark.parametrize(
    "in_container,java_args,check_app_exited",
    [
        (False, [], False),  # default
        (False, ["-XX:ErrorFile=/tmp/my_custom_error_file.log"], False),  # custom error file
        (True, [], False),  # containerized (other params are ignored)
    ],
)
def test_hotspot_error_file(
    application_pid: int, tmp_path: Path, monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    start_async_profiler = AsyncProfiledProcess.start_async_profiler

    # Simulate crashing process
    def sap_and_crash(self: AsyncProfiledProcess, *args: Any, **kwargs: Any) -> bool:
        result = start_async_profiler(self, *args, **kwargs)
        self.process.send_signal(signal.SIGBUS)
        return result

    monkeypatch.setattr(AsyncProfiledProcess, "start_async_profiler", sap_and_crash)

    # increased duration - give the JVM some time to write the hs_err file.
    profiler = make_java_profiler(storage_dir=str(tmp_path), duration=10)
    with profiler:
        profiler.snapshot()

    assert "Found Hotspot error log" in caplog.text
    assert "OpenJDK" in caplog.text
    assert "SIGBUS" in caplog.text
    assert "libpthread.so" in caplog.text
    assert "memory_usage_in_bytes:" in caplog.text
    assert "Java profiling has been disabled, will avoid profiling any new java process" in caplog.text
    assert profiler._safemode_disable_reason is not None


def test_disable_java_profiling(
    application_pid: int, tmp_path: Path, monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    profiler = make_java_profiler(storage_dir=str(tmp_path))
    dummy_reason = "dummy reason"
    monkeypatch.setattr(profiler, "_safemode_disable_reason", dummy_reason)
    with profiler:
        collapsed = snapshot_one_collaped(profiler)
        assert collapsed == Counter({f"java;[Profiling skipped: disabled due to {dummy_reason}]": 1})

    assert "Java profiling has been disabled, skipping profiling of all java process" in caplog.text


def test_already_loaded_async_profiler_profiling_failure(
    tmp_path: Path, monkeypatch: MonkeyPatch, caplog: LogCaptureFixture, application_pid: int
) -> None:
    with monkeypatch.context() as m:
        import gprofiler.profilers.java

        m.setattr(gprofiler.profilers.java, "POSSIBLE_AP_DIRS", ("/tmp/fake_gprofiler_tmp",))
        with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
            profiler.snapshot()

    with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
        process = profiler._select_processes_to_profile()[0]
        assert any("/tmp/fake_gprofiler_tmp" in mmap.path for mmap in process.memory_maps())
        collapsed = snapshot_one_collaped(profiler)
        assert collapsed == Counter({"java;[Profiling skipped: async-profiler is already loaded]": 1})
        assert "Non-gProfiler async-profiler is already loaded to the target process" in caplog.text


# test only once; and don't test in container - as it will go down once we kill the Java app.
@pytest.mark.parametrize("in_container", [False])
@pytest.mark.parametrize("check_app_exited", [False])  # we're killing it, the exit check will raise.
def test_async_profiler_output_written_upon_jvm_exit(
    tmp_path: Path, application_pid: int, assert_collapsed: AssertInCollapsed, caplog: LogCaptureFixture
) -> None:
    """
    Make sure async-profiler writes output upon process exit (and we manage to read it correctly)
    """
    caplog.set_level(logging.DEBUG)

    with make_java_profiler(storage_dir=str(tmp_path), duration=10) as profiler:

        def delayed_kill() -> None:
            time.sleep(3)
            os.kill(application_pid, signal.SIGINT)

        threading.Thread(target=delayed_kill).start()

        process_collapsed = snapshot_one_collaped(profiler)
        assert_collapsed(process_collapsed)

        assert f"Profiled process {application_pid} exited before stopping async-profiler" in caplog.text


# test only once
@pytest.mark.parametrize("in_container", [False])
def test_async_profiler_stops_after_given_timeout(
    tmp_path: Path, application_pid: int, assert_collapsed: AssertInCollapsed, caplog: LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    process = psutil.Process(application_pid)
    timeout_s = 5
    with AsyncProfiledProcessForTests(
        process=process,
        storage_dir=str(tmp_path),
        stop_event=Event(),
        buildids=False,
        mode="itimer",
        ap_safemode=0,
        ap_args="",
    ) as ap_proc:
        assert ap_proc.start_async_profiler(frequency_to_ap_interval(11), ap_timeout=timeout_s)

        ap_proc.status_async_profiler()
        assert "Profiling is running for " in cast_away_optional(ap_proc.read_output())

        # let the timeout trigger
        time.sleep(timeout_s)

        ap_proc.status_async_profiler()
        assert "Profiler is not active\n" in cast_away_optional(ap_proc.read_output())


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("image_suffix", ["_j9"])
def test_sanity_j9(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
) -> None:
    with make_java_profiler(
        frequency=99,
        storage_dir=str(tmp_path),
        java_async_profiler_mode="itimer",
    ) as profiler:
        assert "OpenJ9" in get_java_version(psutil.Process(application_pid), profiler._stop_event)
        process_collapsed = snapshot_one_collaped(profiler)
        assert_collapsed(process_collapsed)


@pytest.mark.xfail(
    reason="AP 2.7 doesn't support, see https://github.com/jvm-profiling-tools/async-profiler/issues/572"
    " we will fix after that's closed."
)
# test only once. in a container, so that we don't mess up the environment :)
@pytest.mark.parametrize("in_container", [True])
def test_java_deleted_libjvm(tmp_path: Path, application_pid: int, assert_collapsed: AssertInCollapsed) -> None:
    """
    Tests that we can profile processes whose libjvm was deleted, e.g because Java was upgraded.
    """
    assert not is_libjvm_deleted(application_pid)
    # simulate upgrade process - file is removed and replaced by another one.
    libjvm = get_libjvm_path(application_pid)
    libjvm_tmp = libjvm + "."
    shutil.copy(libjvm, libjvm_tmp)
    os.unlink(libjvm)
    os.rename(libjvm_tmp, libjvm)
    assert is_libjvm_deleted(application_pid)

    with make_java_profiler(storage_dir=str(tmp_path), duration=3) as profiler:
        process_collapsed = snapshot_one_collaped(profiler)
        assert_collapsed(process_collapsed)


# test only in a container so that we don't mess with the environment.
@pytest.mark.parametrize("in_container", [True])
def test_java_async_profiler_buildids(
    tmp_path: Path, application_pid: int, assert_collapsed: AssertInCollapsed
) -> None:
    """
    Tests that async-profiler's buildid feature works.
    """
    libc = get_lib_path(application_pid, "/libc-")
    buildid = get_elf_buildid(libc)

    with make_java_profiler(
        storage_dir=str(tmp_path), duration=3, frequency=99, java_async_profiler_buildids=True
    ) as profiler:
        process_collapsed = snapshot_one_collaped(profiler)
        # path buildid+0xoffset_[bid]
        # we check for libc because it has undefined symbols in all profiles :shrug:
        assert_function_in_collapsed(
            f"{remove_prefix(libc, f'/proc/{application_pid}/root/')} {buildid}+0x", process_collapsed
        )
        assert_function_in_collapsed("_[bid]", process_collapsed)


@pytest.mark.parametrize(
    "extra_application_docker_mounts",
    [
        pytest.param([docker.types.Mount(target="/tmp", source="", type="tmpfs", read_only=False)], id="noexec"),
    ],
)
def test_java_noexec_dirs(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    caplog: LogCaptureFixture,
    noexec_tmp_dir: str,
    in_container: bool,
    monkeypatch: MonkeyPatch,
) -> None:
    """
    Tests that gProfiler is able to select a non-default directory for libasyncProfiler if the default one
    is noexec, both container and host.
    """
    caplog.set_level(logging.DEBUG)

    if not in_container:
        import gprofiler.profilers.java

        run_dir = gprofiler.profilers.java.POSSIBLE_AP_DIRS[1]
        assert run_dir.startswith("/run")
        # noexec_tmp_dir won't work and gprofiler will try using run_dir
        # this is done because modifying /tmp on a live system is not legit (we need to create a new tmpfs
        # mount because /tmp is not necessarily tmpfs; and that'll hide all current files in /tmp).
        monkeypatch.setattr(gprofiler.profilers.java, "POSSIBLE_AP_DIRS", (noexec_tmp_dir, run_dir))

    with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
        assert_collapsed(snapshot_one_collaped(profiler))

    # should use this path instead of /tmp/gprofiler_tmp/...
    assert "/run/gprofiler_tmp/async-profiler-" in caplog.text


@pytest.mark.parametrize("in_container", [True])
def test_java_symlinks_in_paths(
    tmp_path: Path,
    application_pid: int,
    application_docker_container: Container,
    assert_collapsed: AssertInCollapsed,
    caplog: LogCaptureFixture,
) -> None:
    """
    Tests that gProfiler correctly reads through symlinks in other namespaces (i.e where special
    treatment is required for /proc/pid/root paths), and that profiling works eventually.
    This basicaly tests the function resolve_proc_root_links().
    """
    caplog.set_level(logging.DEBUG)

    # build this structure
    # /run/final_tmp
    # /run/step2 -> final_tmp
    # /run/step1 -> step2
    # /run/tmpy -> /run/step1
    # /tmp -> /run/tmp
    application_docker_container.exec_run(
        [
            "sh",
            "-c",
            "mkdir -p /run/final_tmp && "
            "ln -s final_tmp /run/step2 && "  # test relative path
            "ln -s step2 /run/step1 && "
            "ln -s /run/step1 /run/tmpy && "  # test absolute path
            "rm -r /tmp && "
            "ln -s /run/tmpy /tmp && "
            "chmod 0777 /tmp /run/final_tmp && chmod +t /tmp/final_tmp",
        ],
        privileged=True,
        user="root",
    )

    with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
        assert_collapsed(snapshot_one_collaped(profiler))

    # part of the commandline to AP - which shall include the final, resolved path.
    assert "load /run/final_tmp/gprofiler_tmp/" in caplog.text
