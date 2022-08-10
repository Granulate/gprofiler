#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import subprocess
from pathlib import Path

from docker import DockerClient
from docker.models.images import Image

from tests.utils import start_gprofiler_in_container_for_one_session, wait_for_container, wait_for_log


def test_controlller_pid(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
) -> None:
    proc = subprocess.Popen(["sleep", "99999999"])

    container = start_gprofiler_in_container_for_one_session(
        docker_client,
        gprofiler_docker_image,
        output_directory,
        output_collapsed,
        [],
        ["-d", "3", "-c", "--controller-pid", str(proc.pid)],
    )

    cycle_log = r"INFO: gprofiler: Saved flamegraph to "
    # wait one gprofiler cycle, to ensure gprofiler starts
    wait_for_log(container, cycle_log)
    # wait another to ensure it doesn't go down
    wait_for_log(container, rf"{cycle_log}.*{cycle_log}")

    # stop the processes
    proc.kill()
    proc.wait()

    # wait for gprofiler to identify it
    wait_for_log(container, f"Controller process {proc.pid} has exited; gProfiler stopping...")

    wait_for_container(container)
