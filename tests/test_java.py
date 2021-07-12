#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import time
from pathlib import Path
from subprocess import Popen
from typing import Callable, List, Mapping, Optional

import psutil
import pytest  # type: ignore
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from gprofiler.java import AsyncProfiledProcess
from gprofiler.merge import parse_one_collapsed
from tests.utils import run_gprofiler_in_container


# adds the "status" command to AsyncProfiledProcess from gProfiler.
class AsyncProfiledProcessForTests(AsyncProfiledProcess):
    def status_async_profiler(self):
        self._run_async_profiler(self._get_base_cmd() + [f"status,log={self._log_path_process}"])


@pytest.mark.parametrize("runtime", ["java"])
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
    # run Java only (just so initialization is faster w/o Python/PHP) for 1000 seconds
    args = ["-v", "-d", "1000", "-o", inner_output_directory, "--no-php", "--no-python"] + runtime_specific_args

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
            container.remove(force=True)

    # run "status"
    proc = psutil.Process(application_pid)
    with AsyncProfiledProcessForTests(proc, tmp_path) as ap_proc:
        ap_proc.status_async_profiler()
    # printed the process' stdout, see ACTION_STATUS case in async-profiler/profiler.cpp
    expected_message = b"Profiling is running for "

    assert any("libasyncProfiler.so" in m.path for m in proc.memory_maps())

    # container case
    if application_docker_container is not None:
        assert expected_message in application_docker_container.logs()
    # else, process
    else:
        assert application_process is not None
        assert application_process.stdout is not None
        # mypy complains about read1
        assert expected_message in application_process.stdout.read1(4096)  # type: ignore

    # then start again, with 1 second
    assert args[2] == "1000"
    args[2] = "1"
    _, logs = run_gprofiler_in_container(docker_client, gprofiler_docker_image, args, volumes=volumes)

    assert "Found async-profiler already started" in logs

    collapsed = parse_one_collapsed(Path(output_directory / "last_profile.col").read_text())
    assert_collapsed(collapsed)
