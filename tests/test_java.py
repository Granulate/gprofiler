#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import signal
import time
from pathlib import Path
from subprocess import Popen
from threading import Event
from typing import Callable, List, Mapping, Optional

import psutil
import pytest  # type: ignore
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from gprofiler.merge import parse_one_collapsed
from gprofiler.profilers.java import AsyncProfiledProcess, JavaProfiler
from tests.utils import assert_function_in_collapsed, run_gprofiler_in_container


# adds the "status" command to AsyncProfiledProcess from gProfiler.
class AsyncProfiledProcessForTests(AsyncProfiledProcess):
    def status_async_profiler(self):
        self._run_async_profiler(
            self._get_base_cmd() + [f"status,log={self._log_path_process},file={self._output_path_process}"]
        )


@pytest.fixture
def runtime() -> str:
    return "java"


def test_java_async_profiler_stopped(
    docker_client: DockerClient,
    application_pid: int,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    assert_collapsed: Callable[[Mapping[str, int]], None],
    tmp_path: str,
    application_docker_container: Optional[Container],
    application_process: Optional[Popen],
) -> None:
    """
    This test runs gProfiler, targeting a Java application. Then kills gProfiler brutally so profiling doesn't
    stop gracefully and async-profiler remains active.
    Then runs gProfiler again and makes sure we're able to restart async-profiler and get results normally.
    """

    inner_output_directory = "/tmp/gprofiler"
    volumes = {
        str(output_directory): {"bind": inner_output_directory, "mode": "rw"},
    }
    # run Java only (just so initialization is faster w/o others) for 1000 seconds
    args = [
        "-v",
        "-d",
        "1000",
        "-o",
        inner_output_directory,
        "--no-php",
        "--no-python",
        "--no-ruby",
        "--perf-mode=none",
    ] + runtime_specific_args

    container = None
    try:
        container, logs = run_gprofiler_in_container(
            docker_client, gprofiler_docker_image, args, volumes=volumes, auto_remove=False, detach=True
        )
        assert container is not None, "got None container?"

        # and stop after a short while, brutally.
        time.sleep(10)
        container.kill("SIGKILL")
    finally:
        if container is not None:
            print("gProfiler container logs:", container.logs().decode(), sep="\n")
            container.remove(force=True)

    proc = psutil.Process(application_pid)
    assert any("libasyncProfiler.so" in m.path for m in proc.memory_maps())

    # run "status"
    with AsyncProfiledProcessForTests(proc, tmp_path, False, mode="itimer", safemode=0) as ap_proc:
        ap_proc.status_async_profiler()

        # printed the output file, see ACTION_STATUS case in async-profiler/profiler.cpp\
        assert "Profiling is running for " in ap_proc.read_output()

    # then start again, with 1 second
    assert args[2] == "1000"
    args[2] = "1"
    _, logs = run_gprofiler_in_container(docker_client, gprofiler_docker_image, args, volumes=volumes)

    assert "Found async-profiler already started" in logs

    collapsed = parse_one_collapsed(Path(output_directory / "last_profile.col").read_text())
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
    with JavaProfiler(
        1000,
        1,
        Event(),
        str(tmp_path),
        False,
        True,
        java_async_profiler_mode="cpu",
        java_async_profiler_safemode=0,
        java_mode="ap",
    ) as profiler:
        process_collapsed = profiler.snapshot().get(application_pid)
        assert_collapsed(process_collapsed, check_comm=True)
        assert_function_in_collapsed(
            "do_syscall_64_[k]", "java", process_collapsed, True
        )  # ensure kernels stacks exist


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
    with JavaProfiler(
        1000,
        1,
        Event(),
        str(tmp_path),
        False,
        True,
        java_async_profiler_mode="cpu",
        java_async_profiler_safemode=0,
        java_mode="ap",
    ) as profiler:
        process_collapsed = profiler.snapshot().get(application_pid)
        assert_collapsed(process_collapsed, check_comm=True)
        assert_function_in_collapsed(
            "do_syscall_64_[k]", "java", process_collapsed, True
        )  # ensure kernels stacks exist


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

    with JavaProfiler(1, 5, Event(), str(tmp_path), False, False, "cpu", 0, "ap") as profiler:
        profiler.snapshot()

    assert len(caplog.records) > 0
    message = caplog.records[0].message
    assert "Found Hotspot error log" in message
    assert "OpenJDK" in message
    assert "SIGBUS" in message
    assert "libpthread.so" in message
    assert "memory_usage_in_bytes:" in message
