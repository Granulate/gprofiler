#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import json
from pathlib import Path
from typing import Dict, List, Tuple

import pytest
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from gprofiler.merge import parse_one_collapsed
from tests.conftest import AssertInCollapsed
from tests.utils import run_gprofiler_in_container_for_one_session


@pytest.mark.parametrize(
    "in_container,runtime,profiler_type,expected_metadata",
    [
        (
            True,
            "python",
            "pyperf",
            {
                "exe": "/usr/local/bin/python3.6",
                "execfn": "/usr/local/bin/python",
                "libpython_elfid": "buildid:0ef3fce0ef90d8f40ad9236793d30081001ee898",
                "exe_elfid": "buildid:a04b9016e15a247fbc21c91260c13e17a458ed33",
                "python_version": "Python 3.6.15",
                "sys_maxunicode": None,
            },
        ),
        (
            True,
            "ruby",
            "rbspy",
            {
                "exe": "/usr/local/bin/ruby",
                "execfn": "/usr/local/bin/ruby",
                "libruby_elfid": "buildid:bf7da94bfdf3cb595ae0af450112076bdaaabee8",
                "exe_elfid": "buildid:cbc0ab21749fe48b904fff4e73b88413270bd8ba",
                "ruby_version": "ruby 2.6.7p197 (2021-04-05 revision 67941) [x86_64-linux]",
            },
        ),
        (
            True,
            "java",
            "ap",
            {
                "exe": "/usr/local/openjdk-8/bin/java",
                "execfn": "/usr/local/openjdk-8/bin/java",
                "java_version": 'openjdk version "1.8.0_275"\n'
                "OpenJDK Runtime Environment (build 1.8.0_275-b01)\n"
                "OpenJDK 64-Bit Server VM (build 25.275-b01, mixed mode)",
                "libjvm_elfid": "buildid:0542486ff00153ca0bcf9f2daea9a36c428d6cde",
            },
        ),
        (
            True,
            "golang",
            "perf",
            {
                "exe": "/app/fibonacci",
                "execfn": "./fibonacci",
                "golang_version": "go1.18.3",
                "link": "dynamic",
                "libc": "glibc",
            },
        ),
        (
            True,
            "nodejs",
            "perf",
            {
                "exe": "/usr/local/bin/node",
                "execfn": "/usr/local/bin/node",
                "node_version": "v10.24.1",
                "link": "dynamic",
                "libc": "glibc",
            },
        ),
        (
            True,
            "dotnet",
            "dotnet-trace",
            {
                "dotnet_version": "6.0.302",
                "exe": "/usr/share/dotnet/dotnet",
                "execfn": "/usr/bin/dotnet",
            },
        ),
    ],
)
def test_app_metadata(
    docker_client: Tuple[DockerClient, str],
    application_docker_container: Container,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    assert_collapsed: AssertInCollapsed,
    profiler_flags: List[str],
    expected_metadata: Dict,
    application_executable: str,
) -> None:
    run_gprofiler_in_container_for_one_session(
        docker_client,
        gprofiler_docker_image,
        output_directory,
        output_collapsed,
        runtime_specific_args,
        profiler_flags,
    )
    collapsed_text = Path(output_directory / "last_profile.col").read_text()
    # sanity
    collapsed = parse_one_collapsed(collapsed_text)
    assert_collapsed(collapsed)

    # check the metadata
    lines = collapsed_text.splitlines()
    assert lines[0].startswith("#")
    metadata = json.loads(lines[0][1:])

    assert application_docker_container.name in metadata["containers"]
    # find its app metadata index - find a stack line from the app of this container
    stack = next(filter(lambda l: application_docker_container.name in l and application_executable in l, lines[1:]))
    # stack begins with index
    idx = int(stack.split(";")[0])

    # values from the current test container
    assert metadata["application_metadata"][idx] == expected_metadata
