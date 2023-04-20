#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import shutil
from pathlib import Path
from subprocess import Popen
from typing import Callable, List, Mapping, Optional

import pytest
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from gprofiler.utils.collapsed_format import parse_one_collapsed
from tests.utils import RUNTIME_PROFILERS, _no_errors, is_aarch64, run_gprofiler_in_container


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
    runtime: str,
) -> None:
    _ = application_pid  # Fixture only used for running the application.

    if runtime == "php":
        pytest.skip("Flaky, https://github.com/Granulate/gprofiler/issues/630")

    if runtime == "python" and any("pyperf" in flag for flag in profiler_flags) and is_aarch64():
        pytest.xfail("PyPerf doesn't run on Aarch64 - https://github.com/Granulate/gprofiler/issues/499")

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
        assert popen.wait() == 0

    collapsed = parse_one_collapsed(Path(output_directory / "last_profile.col").read_text())
    assert_collapsed(collapsed)


@pytest.mark.parametrize("application_docker_mount", [True])  # adds output_directory mount to the application container
@pytest.mark.parametrize(
    "runtime,profiler_type",
    [
        ("java", "ap"),
        ("python", "py-spy"),
        ("ruby", "rbspy"),
        ("php", "phpspy"),
        ("dotnet", "dotnet-trace"),
    ],
)
@pytest.mark.parametrize(
    "application_docker_capabilities",
    [
        [
            "SYS_PTRACE",
        ]
    ],
)
@pytest.mark.parametrize("in_container", [True])
def test_executable_not_privileged(
    gprofiler_exe: Path,
    application_docker_container: Container,
    runtime_specific_args: List[str],
    assert_collapsed: Callable[[Mapping[str, int]], None],
    output_directory: Path,
    profiler_flags: List[str],
    application_docker_mount: bool,
) -> None:
    """
    Tests gProfiler with lower privileges: runs in a container, as root & with SYS_PTRACE,
    but nothing more.
    """
    os.makedirs(str(output_directory), mode=0o755, exist_ok=True)

    mount_gprofiler_exe = str(output_directory / "gprofiler")
    if not os.path.exists(mount_gprofiler_exe):
        shutil.copy(str(gprofiler_exe), mount_gprofiler_exe)

    command = (
        [
            mount_gprofiler_exe,
            "-v",
            "--output-dir",
            str(output_directory),
            "--disable-pidns-check",  # not running in init PID
            "--no-perf",  # no privileges to run perf
        ]
        + runtime_specific_args
        + profiler_flags
    )
    exit_code, output = application_docker_container.exec_run(cmd=command, privileged=False, user="root:root")

    print(output.decode())
    _no_errors(output.decode())
    assert exit_code == 0

    collapsed = parse_one_collapsed(Path(output_directory / "last_profile.col").read_text())
    assert_collapsed(collapsed)
