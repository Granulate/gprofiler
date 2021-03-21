#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from glob import glob
from pathlib import Path
from typing import Callable, Union, Mapping

from docker import DockerClient
from docker.models.images import Image

from gprofiler.java import JavaProfiler
from gprofiler.python import PythonProfiler
from gprofiler.merge import parse_collapsed


def test_from_host(
    application_pid: int,
    profiler: Union[JavaProfiler, PythonProfiler],
    assert_collapsed: Callable[[Mapping[str, int]], None],
) -> None:
    process_collapsed = profiler.profile_processes()
    assert_collapsed(process_collapsed.get(application_pid))


def test_from_container(
    docker_client: DockerClient,
    application_pid: int,
    gprofiler_docker_image: Image,
    output_directory: Path,
    assert_collapsed: Callable[[Mapping[str, int]], None],
) -> None:
    _ = application_pid  # Fixture only used for running the application.
    inner_output_directory = "/tmp/gpofiler"
    docker_uds = "/var/run/docker.sock"
    docker_client.containers.run(
        gprofiler_docker_image,
        ["-d", "1", "-o", inner_output_directory],
        privileged=True,
        network_mode="host",
        pid_mode="host",
        userns_mode="host",
        volumes={
            docker_uds: {"bind": docker_uds, "mode": "rw"},
            str(output_directory): {"bind": inner_output_directory, "mode": "rw"},
        },
        auto_remove=True,
    )
    output = glob(str(output_directory / "*.col"))
    assert len(output) == 1
    collapsed_path = output[0]
    collapsed = parse_collapsed(Path(collapsed_path).read_text())
    assert_collapsed(collapsed)
