#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import json
from pathlib import Path
from typing import List

import pytest
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from gprofiler.merge import parse_one_collapsed
from tests.conftest import AssertInCollapsed
from tests.utils import run_gprofiler_container


@pytest.mark.parametrize(
    "in_container,runtime,profiler_type",
    [
        (True, "python", "pyperf"),
    ],
)
def test_python_app_metadata(
    docker_client: DockerClient,
    application_docker_container: Container,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    assert_collapsed: AssertInCollapsed,
    profiler_flags: List[str],
) -> None:
    run_gprofiler_container(
        docker_client, gprofiler_docker_image, output_directory, runtime_specific_args, profiler_flags
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
    # find its app metadata index - find a stack line from the python app of this container
    stack = next(filter(lambda l: application_docker_container.name in l and "python" in l, lines[1:]))
    # stack begins with index
    idx = int(stack.split(";")[0])

    # values from the current test container
    assert metadata["application_metadata"][idx] == {
        "exe": "/usr/local/bin/python3.6",
        "execfn": "/usr/local/bin/python",
        "libpython_elfid": "buildid:0ef3fce0ef90d8f40ad9236793d30081001ee898",
        "python_elfid": "buildid:a04b9016e15a247fbc21c91260c13e17a458ed33",
        "python_version": "Python 3.6.15",
        "sys_maxunicode": None,
    }
