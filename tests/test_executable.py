#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from pathlib import Path
from subprocess import Popen
from typing import Callable, List, Mapping

import pytest  # type: ignore
from docker import DockerClient
from docker.models.images import Image

from gprofiler.merge import parse_one_collapsed
from tests.utils import RUNTIME_PROFILERS, run_gprofiler_in_container


@pytest.mark.parametrize(
    "runtime,profiler_type",
    RUNTIME_PROFILERS,
)
def test_executable(
    gprofiler_exe: Path,
    application_pid: int,
    runtime_specific_args: List[str],
    assert_collapsed: Callable[[Mapping[str, int]], None],
    exec_container_image: Optional[Image],
    docker_client: DockerClient,
    output_directory: Path,
    profiler_flags: List[str],
) -> None:
    _ = application_pid  # Fixture only used for running the application.

    if exec_container_image is not None:
        if "centos:6" in exec_container_image.tags and any("pyperf" in flag for flag in profiler_flags):
            # don't run PyPerf on the centos:6 image, it fails. And in any case PyPerf can't run on centos:6.
            pytest.skip("PyPerf test on centos:6")

        gprofiler_inner_dir = Path("/app")
        inner_output_dir = Path("/app/output")
        cwd = Path(os.getenv("GITHUB_WORKSPACE", os.getcwd()))
        volumes = {
            str(output_directory): {"bind": str(inner_output_dir), "mode": "rw"},
            str(cwd): {"bind": str(gprofiler_inner_dir), "mode": "rw"},
        }

        command = (
            [
                str(gprofiler_inner_dir / gprofiler_exe),
                "-v",
                "--output-dir",
                str(inner_output_dir),
            ]
            + runtime_specific_args
            + profiler_flags
        )
        run_gprofiler_in_container(docker_client, exec_container_image, command=command, volumes=volumes)
    else:
        os.mkdir(output_directory)
        command = (
            ["sudo", str(gprofiler_exe), "--output-dir", str(output_directory), "-d", "5"]
            + runtime_specific_args
            + profiler_flags
        )
        popen = Popen(command)
        popen.wait()

    collapsed = parse_one_collapsed(Path(output_directory / "last_profile.col").read_text())
    assert_collapsed(collapsed)
