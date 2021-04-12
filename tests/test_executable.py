#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from typing import Callable, Mapping
from glob import glob
from pathlib import Path
from subprocess import Popen

import pytest
from docker import DockerClient
from docker.models.images import Image

from gprofiler.merge import parse_collapsed
from tests.util import run_privileged_container


@pytest.mark.parametrize('runtime', ['java', 'python'])
def test_from_executable(
    gprofiler_exe: Path,
    application_pid: int,
    assert_collapsed: Callable[[Mapping[str, int]], None],
    exec_container_image: Image,
    docker_client: DockerClient,
    output_directory: Path,
) -> None:
    _ = application_pid  # Fixture only used for running the application.

    if exec_container_image is not None:
        gprofiler_inner_dir = Path("/app")
        inner_output_dir = Path("/app/output")
        cwd = Path(os.getenv('GITHUB_WORKSPACE', os.getcwd()))
        volumes = {str(output_directory): {"bind": str(inner_output_dir), "mode": "rw"},
                   str(cwd): {"bind": str(gprofiler_inner_dir), "mode": "rw"}}

        command = [str(gprofiler_inner_dir / gprofiler_exe), "--output-dir", str(inner_output_dir)]
        run_privileged_container(docker_client, exec_container_image, command=command, volumes=volumes)
    else:
        os.mkdir(output_directory)
        popen = Popen(["sudo", gprofiler_exe, "--output-dir", output_directory])
        popen.wait()

    output = glob(str(output_directory / "*.col"))
    assert len(output) == 1
    collapsed_path = output[0]
    collapsed = parse_collapsed(Path(collapsed_path).read_text())
    assert_collapsed(collapsed)
