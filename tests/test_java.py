#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import os
import shutil
import signal
import subprocess
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
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image
from granulate_utils.java import parse_jvm_version
from granulate_utils.linux.ns import get_process_nspid
from granulate_utils.linux.process import is_musl
from packaging.version import Version
from pytest import LogCaptureFixture, MonkeyPatch

from gprofiler.profilers.java import (
    JAVA_SAFEMODE_ALL,
    AsyncProfiledProcess,
    JavaProfiler,
    frequency_to_ap_interval,
    get_java_version,
)
from tests.conftest import AssertInCollapsed
from tests.type_utils import cast_away_optional
from tests.utils import (
    _application_docker_container,
    assert_function_in_collapsed,
    is_function_in_collapsed,
    is_pattern_in_collapsed,
    make_java_profiler,
    snapshot_pid_collapsed,
    snapshot_pid_profile,
)


@pytest.fixture
def runtime() -> str:
    return "java"


def get_lib_path(application_pid: int, path: str) -> str:
    libs = set()
    for m in psutil.Process(application_pid).memory_maps():
        if path in m.path:
            libs.add(m.path)
    assert len(libs) == 1, f"found {libs!r} - expected 1"
    return f"/proc/{application_pid}/root/{libs.pop()}"


def get_libjvm_path(application_pid: int) -> str:
    return get_lib_path(application_pid, "/libjvm.so")


def _read_pid_maps(pid: int) -> str:
    return Path(f"/proc/{pid}/maps").read_text()


def is_libjvm_deleted(application_pid: int) -> bool:
    # can't use get_libjvm_path() - psutil removes "deleted" if the file actually exists...
    return "/libjvm.so (deleted)" in _read_pid_maps(application_pid)


# adds the "status" command to AsyncProfiledProcess from gProfiler.
class AsyncProfiledProcessForTests(AsyncProfiledProcess):
    def status_async_profiler(self) -> None:
        self._run_async_profiler(
            self._get_base_cmd() + [f"status,log={self._log_path_process},file={self._output_path_process}"],
        )


def test_async_profiler_already_running(
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    tmp_path_world_accessible: Path,
    caplog: LogCaptureFixture,
) -> None:
    """
    Test we're able to restart async-profiler in case it's already running in the process and get results normally.
    """
    caplog.set_level(logging.INFO)
    with make_java_profiler(storage_dir=str(tmp_path_world_accessible)) as profiler:
        process = profiler._select_processes_to_profile()[0]

        with AsyncProfiledProcess(
            process=process,
            storage_dir=profiler._storage_dir,
            insert_dso_name=False,
            stop_event=profiler._stop_event,
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
            insert_dso_name=False,
            stop_event=profiler._stop_event,
            mode="itimer",
            ap_safemode=0,
            ap_args="",
        ) as ap_proc:
            ap_proc.status_async_profiler()
            # printed the output file, see ACTION_STATUS case in async-profiler/profiler.cpp
            assert "Profiling is running for " in cast_away_optional(ap_proc.read_output())

        # then start again
        collapsed = snapshot_pid_collapsed(profiler, application_pid)
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
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)
        assert_function_in_collapsed("do_syscall_64_[k]", process_collapsed)  # ensure kernels stacks exist


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("application_image_tag", ["musl"])
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

        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)
        assert_function_in_collapsed("do_syscall_64_[k]", process_collapsed)  # ensure kernels stacks exist

        # make sure libstdc++ and libgcc are not loaded - the running Java does not require them,
        # and neither should our async-profiler build.
        maps = Path(f"/proc/{application_pid}/maps").read_text()
        assert "/libstdc++.so" not in maps
        assert "/libgcc_s.so" not in maps


def test_java_safemode_parameters(tmp_path: Path) -> None:
    with pytest.raises(AssertionError) as excinfo:
        make_java_profiler(storage_dir=str(tmp_path), java_version_check=False)
    assert "Java version checks are mandatory in --java-safemode" in str(excinfo.value)


def test_java_safemode_version_check(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
    application_pid: int,
    application_docker_container: Container,
    application_process: Optional[Popen],
) -> None:
    monkeypatch.setitem(JavaProfiler.MINIMAL_SUPPORTED_VERSIONS, 8, (Version("8.999"), 0))

    with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
        process = profiler._select_processes_to_profile()[0]
        jvm_version = parse_jvm_version(get_java_version(process, profiler._stop_event))
        collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert collapsed == Counter({"java;[Profiling skipped: profiling this JVM is not supported]": 1})

    log_record = next(filter(lambda r: r.message == "Unsupported JVM version", caplog.records))
    log_extra = log_record.gprofiler_adapter_extra  # type: ignore
    assert log_extra["jvm_version"] == repr(jvm_version)


def test_java_safemode_build_number_check(
    tmp_path: Path,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
    application_pid: int,
    application_docker_container: Container,
    application_process: Optional[Popen],
) -> None:
    with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
        process = profiler._select_processes_to_profile()[0]
        jvm_version = parse_jvm_version(get_java_version(process, profiler._stop_event))
        monkeypatch.setitem(JavaProfiler.MINIMAL_SUPPORTED_VERSIONS, 8, (jvm_version.version, 999))
        collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert collapsed == Counter({"java;[Profiling skipped: profiling this JVM is not supported]": 1})

    log_record = next(filter(lambda r: r.message == "Unsupported JVM version", caplog.records))
    log_extra = log_record.gprofiler_adapter_extra  # type: ignore
    assert log_extra["jvm_version"] == repr(jvm_version)


@pytest.mark.parametrize(
    "in_container,java_args,check_app_exited",
    [
        (False, (), False),  # default
        (False, ("-XX:ErrorFile=/tmp/my_custom_error_file.log",), False),  # custom error file
        (True, (), False),  # containerized (other params are ignored)
    ],
)
def test_hotspot_error_file(
    application_pid: int, tmp_path: Path, monkeypatch: MonkeyPatch, caplog: LogCaptureFixture
) -> None:
    start_async_profiler = AsyncProfiledProcess.start_async_profiler

    # Simulate crashing process
    def start_async_profiler_and_crash(self: AsyncProfiledProcess, *args: Any, **kwargs: Any) -> bool:
        result = start_async_profiler(self, *args, **kwargs)
        self.process.send_signal(signal.SIGBUS)
        return result

    monkeypatch.setattr(AsyncProfiledProcess, "start_async_profiler", start_async_profiler_and_crash)

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
        collapsed = snapshot_pid_collapsed(profiler, application_pid)
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
        collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert collapsed == Counter({"java;[Profiling skipped: async-profiler is already loaded]": 1})
        assert "Non-gProfiler async-profiler is already loaded to the target process" in caplog.text


# test only once; and don't test in container - as it will go down once we kill the Java app.
@pytest.mark.parametrize("in_container", [False])
@pytest.mark.parametrize("check_app_exited", [False])  # we're killing it, the exit check will raise.
def test_async_profiler_output_written_upon_jvm_exit(
    tmp_path_world_accessible: Path,
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    caplog: LogCaptureFixture,
) -> None:
    """
    Make sure async-profiler writes output upon process exit (and we manage to read it correctly)
    """
    caplog.set_level(logging.DEBUG)

    with make_java_profiler(storage_dir=str(tmp_path_world_accessible), duration=10) as profiler:

        def delayed_kill() -> None:
            time.sleep(3)
            os.kill(application_pid, signal.SIGINT)

        threading.Thread(target=delayed_kill).start()

        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)

        assert f"Profiled process {application_pid} exited before stopping async-profiler" in caplog.text


# test only once
@pytest.mark.parametrize("in_container", [False])
def test_async_profiler_stops_after_given_timeout(
    tmp_path_world_accessible: Path,
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    caplog: LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)

    process = psutil.Process(application_pid)
    timeout_s = 5
    with AsyncProfiledProcessForTests(
        process=process,
        storage_dir=str(tmp_path_world_accessible),
        insert_dso_name=False,
        stop_event=Event(),
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
@pytest.mark.parametrize("application_image_tag,search_for", [("j9", "OpenJ9"), ("zing", "Zing")])
def test_sanity_other_jvms(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    search_for: str,
) -> None:
    with make_java_profiler(
        frequency=99,
        storage_dir=str(tmp_path),
        java_async_profiler_mode="cpu",
    ) as profiler:
        assert search_for in get_java_version(psutil.Process(application_pid), profiler._stop_event)
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)


# test only once. in a container, so that we don't mess up the environment :)
@pytest.mark.parametrize("in_container", [True])
def test_java_deleted_libjvm(
    tmp_path: Path, application_pid: int, application_docker_container: Container, assert_collapsed: AssertInCollapsed
) -> None:
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
    assert is_libjvm_deleted(
        application_pid
    ), f"Not (deleted) after deleting? libjvm={libjvm} maps={_read_pid_maps(application_pid)}"

    with make_java_profiler(storage_dir=str(tmp_path), duration=3) as profiler:
        process_collapsed = snapshot_pid_collapsed(profiler, application_pid)
        assert_collapsed(process_collapsed)


@pytest.mark.parametrize(
    "extra_application_docker_mounts",
    [
        pytest.param([docker.types.Mount(target="/tmp", source="", type="tmpfs", read_only=False)], id="noexec"),
    ],
)
def test_java_noexec_dirs(
    tmp_path_world_accessible: Path,
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

    with make_java_profiler(storage_dir=str(tmp_path_world_accessible)) as profiler:
        assert_collapsed(snapshot_pid_collapsed(profiler, application_pid))

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
        assert_collapsed(snapshot_pid_collapsed(profiler, application_pid))

    # part of the commandline to AP - which shall include the final, resolved path.
    assert "load /run/final_tmp/gprofiler_tmp/" in caplog.text


@pytest.mark.parametrize("in_container", [True])  # only in container is enough
def test_java_appid_and_metadata_before_process_exits(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed: AssertInCollapsed,
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    """
    Tests that an appid is generated also for a process that exits during profiling
    (i.e, ensure that is is collected before profiling starts)
    """
    caplog.set_level(logging.DEBUG)

    start_async_profiler = AsyncProfiledProcess.start_async_profiler

    # Make the process exit before profiling ends
    def start_async_profiler_and_interrupt(self: AsyncProfiledProcess, *args: Any, **kwargs: Any) -> bool:
        result = start_async_profiler(self, *args, **kwargs)
        time.sleep(3)
        self.process.send_signal(signal.SIGINT)
        return result

    monkeypatch.setattr(AsyncProfiledProcess, "start_async_profiler", start_async_profiler_and_interrupt)

    with make_java_profiler(
        storage_dir=str(tmp_path),
        duration=10,
    ) as profiler:
        profile = snapshot_pid_profile(profiler, application_pid)

    assert_collapsed(profile.stacks)

    # process exited before we've stopped profiling...
    assert f"Profiled process {application_pid} exited before stopping async-profiler" in caplog.text
    # but we have an appid!
    assert profile.appid == "java: Fibonacci.jar"
    # and application metadata for java
    assert profile.app_metadata is not None and "java_version" in profile.app_metadata


@pytest.mark.parametrize("in_container", [True])  # only in container is enough
def test_java_attach_socket_missing(
    tmp_path: Path,
    application_pid: int,
) -> None:
    """
    Tests that we get the proper JattachMissingSocketException when the attach socket is deleted.
    """

    with make_java_profiler(
        storage_dir=str(tmp_path),
        duration=1,
    ) as profiler:
        snapshot_pid_profile(profiler, application_pid)

        # now the attach socket is created, remove it
        Path(f"/proc/{application_pid}/root/tmp/.java_pid{get_process_nspid(application_pid)}").unlink()

        profile = snapshot_pid_profile(profiler, application_pid)
        assert len(profile.stacks) == 1
        assert next(iter(profile.stacks.keys())) == "java;[Profiling error: exception JattachSocketMissingException]"


# we know what messages to expect when in container, not on the host Java
@pytest.mark.parametrize("in_container", [True])
def test_java_jattach_async_profiler_log_output(
    tmp_path: Path,
    application_pid: int,
    caplog: LogCaptureFixture,
) -> None:
    """
    Tests that AP log is collected and logged in gProfiler's log.
    """
    caplog.set_level(logging.DEBUG)
    with make_java_profiler(
        storage_dir=str(tmp_path),
        duration=1,
    ) as profiler:
        # strip the container's libvjm, so we get the AP log message about missing debug
        # symbols when we profile it.
        subprocess.run(["strip", get_libjvm_path(application_pid)], check=True)

        snapshot_pid_profile(profiler, application_pid)

        log_records = list(filter(lambda r: r.message == "async-profiler log", caplog.records))
        assert len(log_records) == 2  # start,stop
        # start
        assert (
            log_records[0].gprofiler_adapter_extra["ap_log"]  # type: ignore
            == "[WARN] Install JVM debug symbols to improve profile accuracy\n"
        )
        # stop
        assert log_records[1].gprofiler_adapter_extra["ap_log"] == ""  # type: ignore


@pytest.mark.parametrize(
    "change_argv0,java_path",
    [
        pytest.param(True, "java", id="argv0 is 'java'"),
        pytest.param(True, "/usr/bin/java", id="argv0 is '/usr/bin/java'"),
        pytest.param(False, "", id="argv0 is not java"),
    ],
)
def test_java_different_basename(
    tmp_path: Path,
    docker_client: DockerClient,
    application_docker_image: Image,
    assert_collapsed: AssertInCollapsed,
    caplog: LogCaptureFixture,
    change_argv0: bool,
    java_path: Optional[str],
) -> None:
    """
    Tests that we can profile a Java app that runs with non-java "comm", by reading the argv0 instead.
    """
    java_notjava_basename = "java-notjava"

    with make_java_profiler(
        storage_dir=str(tmp_path),
        duration=1,
        java_safemode=JAVA_SAFEMODE_ALL,  # explicitly enable, for basename checks
    ) as profiler:
        prefix_exec_func = f"exec -a {java_path} " if change_argv0 else ""
        with _application_docker_container(
            docker_client,
            application_docker_image,
            [],
            [],
            application_docker_command=[
                "bash",
                "-c",
                f"{prefix_exec_func}{java_notjava_basename} -jar Fibonacci.jar",
            ],
        ) as container:
            application_pid = container.attrs["State"]["Pid"]
            profile = snapshot_pid_profile(profiler, application_pid)
            if change_argv0:
                # we changed basename - we should have run the profiler
                assert profile.app_metadata is not None
                assert (
                    os.path.basename(profile.app_metadata["execfn"])
                    == os.path.basename(profile.app_metadata["exe"])
                    == java_notjava_basename
                )
                assert_function_in_collapsed(f"{java_notjava_basename};", profile.stacks)
                assert_collapsed(profile.stacks)
            else:
                # we didn't change basename - we should not have run the profiler due to a different basename.
                assert profile.stacks == Counter(
                    {f"{java_notjava_basename};[Profiling skipped: profiling this JVM is not supported]": 1}
                )
                log_records = list(
                    filter(
                        lambda r: r.message
                        == "Non-java basenamed process (cannot get Java version), skipping... (disable"
                        " --java-safemode=java-extended-version-checks to profile it anyway)",
                        caplog.records,
                    )
                )
                assert len(log_records) == 1
                assert (
                    os.path.basename(log_records[0].gprofiler_adapter_extra["exe"])  # type: ignore
                    == java_notjava_basename
                )


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("insert_dso_name", [False, True])
def test_dso_name_in_ap_profile(
    tmp_path: Path,
    application_pid: int,
    insert_dso_name: bool,
) -> None:
    with make_java_profiler(
        storage_dir=str(tmp_path),
        insert_dso_name=insert_dso_name,
        duration=3,
        frequency=999,
    ) as profiler:
        collapsed = snapshot_pid_profile(profiler, application_pid).stacks
        assert is_function_in_collapsed("jni_NewObject", collapsed)
        assert insert_dso_name == is_pattern_in_collapsed(r"jni_NewObject \(.+?/libjvm.so\)", collapsed)


# test that missing symbol and only DSO name is recognized and handled correctly by async profiler
@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("insert_dso_name", [False, True])
@pytest.mark.parametrize("libc_pattern", [r"(^|;)\(/.*/libc-.*\.so\)($|;)"])
def test_handling_missing_symbol_in_profile(
    tmp_path: Path,
    application_pid: int,
    insert_dso_name: bool,
    libc_pattern: str,
) -> None:
    with make_java_profiler(
        storage_dir=str(tmp_path),
        insert_dso_name=insert_dso_name,
        duration=3,
        frequency=999,
    ) as profiler:
        collapsed = snapshot_pid_profile(profiler, application_pid).stacks
        assert is_pattern_in_collapsed(libc_pattern, collapsed)
