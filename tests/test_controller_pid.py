#
# Copyright (C) 2022 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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

    container = None
    try:
        container = start_gprofiler_in_container_for_one_session(
            docker_client,
            gprofiler_docker_image,
            output_directory,
            output_collapsed,
            [],
            ["-d", "3", "-c", "--controller-pid", str(proc.pid), "--no-java"],
        )

        cycle_log = r"INFO: gprofiler: Saved flamegraph to "
        # wait one gprofiler cycle, to ensure gprofiler starts
        first = wait_for_log(container, cycle_log, 0)
        # wait another to ensure it doesn't go down
        second = wait_for_log(container, cycle_log, first + 1)

        # stop the processes
        proc.kill()
        proc.wait()

        # wait for gprofiler to identify it
        wait_for_log(container, f"Controller process {proc.pid} has exited; gProfiler stopping...", second)

        wait_for_container(container)
    finally:
        if container is not None:
            container.remove(force=True)
