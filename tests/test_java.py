#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import os
import signal
import threading
import time
from collections import Counter
from pathlib import Path
from threading import Event

import psutil
import pytest
from packaging.version import Version

from gprofiler.profilers.java import AsyncProfiledProcess, JavaProfiler, frequency_to_ap_interval, parse_jvm_version
from tests.utils import assert_function_in_collapsed, make_java_profiler, snapshot_one_collaped


# adds the "status" command to AsyncProfiledProcess from gProfiler.
class AsyncProfiledProcessForTests(AsyncProfiledProcess):
    def status_async_profiler(self):
        self._run_async_profiler(
            self._get_base_cmd() + [f"status,log={self._log_path_process},file={self._output_path_process}"],
        )


@pytest.fixture
def runtime() -> str:
    return "java"


def test_async_profiler_already_running(application_pid, assert_collapsed, tmp_path, caplog):
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
            assert "Profiling is running for " in ap_proc.read_output()

        # then start again
        collapsed = snapshot_one_collaped(profiler)
        assert "Found async-profiler already started" in caplog.text
        assert "Finished profiling process" in caplog.text
        assert_collapsed(collapsed)


@pytest.mark.parametrize("in_container", [True])
def test_java_async_profiler_cpu_mode(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed,
) -> None:
    """
    Run Java in a container and enable async-profiler in CPU mode, make sure we get kernel stacks.
    """
    with make_java_profiler(storage_dir=str(tmp_path), frequency=999) as profiler:
        process_collapsed = snapshot_one_collaped(profiler)
        assert_collapsed(process_collapsed)
        assert_function_in_collapsed("do_syscall_64_[k]", process_collapsed)  # ensure kernels stacks exist


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("musl", [True])
def test_java_async_profiler_musl_and_cpu(
    tmp_path: Path,
    application_pid: int,
    assert_collapsed,
) -> None:
    """
    Run Java in an Alpine-based container and enable async-profiler in CPU mode, make sure that musl profiling
    works and that we get kernel stacks.
    """
    with make_java_profiler(storage_dir=str(tmp_path), frequency=999) as profiler:
        process_collapsed = snapshot_one_collaped(profiler)
        assert_collapsed(process_collapsed)
        assert_function_in_collapsed("do_syscall_64_[k]", process_collapsed)  # ensure kernels stacks exist


def test_java_safemode_parameters(tmp_path) -> None:
    with pytest.raises(AssertionError) as excinfo:
        make_java_profiler(storage_dir=str(tmp_path), java_async_profiler_safemode=0)
    assert "async-profiler safemode must be set to 127 in --java-safemode" in str(excinfo.value)

    with pytest.raises(AssertionError) as excinfo:
        make_java_profiler(storage_dir=str(tmp_path), java_version_check=False)
    assert "Java version checks are mandatory in --java-safemode" in str(excinfo.value)


def test_java_safemode_version_check(
    tmp_path, monkeypatch, caplog, application_docker_container, application_process
) -> None:
    monkeypatch.setitem(JavaProfiler.MINIMAL_SUPPORTED_VERSIONS, 8, (Version("8.999"), 0))

    with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
        process = profiler._select_processes_to_profile()[0]
        jvm_version = parse_jvm_version(profiler._get_java_version(process))
        collapsed = snapshot_one_collaped(profiler)
        assert collapsed == Counter({"java;[Profiling skipped: profiling this JVM is not supported]": 1})

    assert next(filter(lambda r: r.message == "Unsupported JVM version", caplog.records)).gprofiler_adapter_extra[
        "jvm_version"
    ] == repr(jvm_version)


def test_java_safemode_build_number_check(
    tmp_path, monkeypatch, caplog, application_docker_container, application_process
) -> None:
    with make_java_profiler(storage_dir=str(tmp_path)) as profiler:
        process = profiler._select_processes_to_profile()[0]
        jvm_version = parse_jvm_version(profiler._get_java_version(process))
        monkeypatch.setitem(JavaProfiler.MINIMAL_SUPPORTED_VERSIONS, 8, (jvm_version.version, 999))
        collapsed = snapshot_one_collaped(profiler)
        assert collapsed == Counter({"java;[Profiling skipped: profiling this JVM is not supported]": 1})

    assert next(filter(lambda r: r.message == "Unsupported JVM version", caplog.records)).gprofiler_adapter_extra[
        "jvm_version"
    ] == repr(jvm_version)


@pytest.mark.parametrize(
    "in_container,java_args,check_app_exited",
    [
        (False, [], False),  # default
        (False, ["-XX:ErrorFile=/tmp/my_custom_error_file.log"], False),  # custom error file
        (True, [], False),  # containerized (other params are ignored)
    ],
)
def test_hotspot_error_file(application_pid, tmp_path, monkeypatch, caplog):
    start_async_profiler = AsyncProfiledProcess.start_async_profiler

    # Simulate crashing process
    def sap_and_crash(self, *args, **kwargs):
        result = start_async_profiler(self, *args, **kwargs)
        self.process.send_signal(signal.SIGBUS)
        return result

    monkeypatch.setattr(AsyncProfiledProcess, "start_async_profiler", sap_and_crash)

    profiler = make_java_profiler(storage_dir=str(tmp_path), duration=5)
    with profiler:
        profiler.snapshot()

    assert "Found Hotspot error log" in caplog.text
    assert "OpenJDK" in caplog.text
    assert "SIGBUS" in caplog.text
    assert "libpthread.so" in caplog.text
    assert "memory_usage_in_bytes:" in caplog.text
    assert "Java profiling has been disabled, will avoid profiling any new java process" in caplog.text
    assert profiler._safemode_disable_reason is not None


def test_disable_java_profiling(application_pid, tmp_path, monkeypatch, caplog):
    caplog.set_level(logging.DEBUG)

    profiler = make_java_profiler(storage_dir=str(tmp_path))
    dummy_reason = "dummy reason"
    monkeypatch.setattr(profiler, "_safemode_disable_reason", dummy_reason)
    with profiler:
        collapsed = snapshot_one_collaped(profiler)
        assert collapsed == Counter({f"java;[Profiling skipped: disabled due to {dummy_reason}]": 1})

    assert "Java profiling has been disabled, skipping profiling of all java process" in caplog.text


def test_already_loaded_async_profiler_profiling_failure(tmp_path, monkeypatch, caplog, application_pid) -> None:
    with monkeypatch.context() as m:
        m.setattr("gprofiler.profilers.java.TEMPORARY_STORAGE_PATH", "/tmp/fake_gprofiler_tmp")
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
def test_async_profiler_output_written_upon_jvm_exit(tmp_path, application_pid, assert_collapsed, caplog) -> None:
    """
    Make sure async-profiler writes output upon process exit (and we manage to read it correctly)
    """
    caplog.set_level(logging.DEBUG)

    with make_java_profiler(storage_dir=str(tmp_path), duration=10) as profiler:

        def delayed_kill():
            time.sleep(3)
            os.kill(application_pid, signal.SIGINT)

        threading.Thread(target=delayed_kill).start()

        process_collapsed = snapshot_one_collaped(profiler)
        assert_collapsed(process_collapsed)

        assert f"Profiled process {application_pid} exited before stopping async-profiler" in caplog.text


# test only once
@pytest.mark.parametrize("in_container", [False])
def test_async_profiler_stops_after_given_timeout(tmp_path, application_pid, assert_collapsed, caplog) -> None:
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
        assert "Profiling is running for " in ap_proc.read_output()

        # let the timeout trigger
        time.sleep(timeout_s)

        ap_proc.status_async_profiler()
        assert "Profiler is not active\n" in ap_proc.read_output()
