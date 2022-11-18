#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import subprocess
from pathlib import Path
from subprocess import Popen
from typing import List

import pytest
from docker import DockerClient
from docker.models.images import Image

from tests.utils import run_privileged_container, _print_process_output


def test_cgroup_limit_container(
        docker_client: DockerClient,
        gprofiler_docker_image: Image,
        output_directory: Path,
) -> None:
    logs = run_privileged_container(docker_client, gprofiler_docker_image,
                                    command=['-v', '--limit-cpu', '0.5', '--limit-memory', '1048576', '-o',
                                             str(output_directory)])

    limit_log = "Trying to set resource limits, cpu='0.5' cores and memory='1024.00' MB."

    assert limit_log not in logs


def test_cgroup_limit_privileged_executable(
    gprofiler_exe: Path,
    output_directory: Path,
) -> None:
    os.mkdir(output_directory)

    command = (
        ['sudo', str(gprofiler_exe), '-v', '--limit-cpu', '0.5',
         '--limit-memory', str((1 << 30)), '-o', str(output_directory), "-d", "5",
         "--no-java", "--no-python", "--no-php", "--no-ruby", "--no-nodejs", "--no-dotnet"]
    )

    popen = Popen(command, stdout=subprocess.PIPE)
    assert popen.wait() == 0
    stdout, _ = popen.communicate()
    logs = stdout.decode("utf-8").splitlines()
    limit_log = "Trying to set resource limits, cpu='0.5' cores and memory='1024.00' MB."

    present = False
    for line in logs:
        if limit_log in line:
            present = True
    assert present


# Not implemented yet.
def test_cgroup_try_limit_no_privileged_executable():
    assert False
