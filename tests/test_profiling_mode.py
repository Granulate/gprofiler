#
# Copyright (C) 2023 Intel Corporation
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
from pathlib import Path
from typing import Container, List

import pytest as pytest
from docker import DockerClient
from docker.models.images import Image

from gprofiler.utils.collapsed_format import parse_one_collapsed
from tests.utils import assert_function_in_collapsed, load_metadata, run_gprofiler_in_container_for_one_session


@pytest.mark.parametrize(
    "profiler_flags,expected_profiling_mode",
    [
        (["--mode=cpu"], "cpu"),
        (["--mode=allocation"], "allocation"),
        # Test default to CPU
        ([], "cpu"),
    ],
)
def test_sanity(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    profiler_flags: List[str],
    expected_profiling_mode: str,
) -> None:
    run_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, output_directory, output_collapsed, [], profiler_flags
    )
    collapsed_text = Path(output_directory / "last_profile.col").read_text()
    assert load_metadata(collapsed_text)["profiling_mode"] == expected_profiling_mode


@pytest.mark.parametrize(
    "runtime, profiler_type, in_container, expected_frame",
    [
        # Async-Profiler produces `java.lang.String[]` style frames only in allocation mode, (method signature doesn't
        # appear in this format)
        ("java", "ap", True, "java.lang.String[]"),
    ],
)
def test_allocation_being_profiled(
    application_docker_container: Container,
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    profiler_flags: List[str],
    runtime: str,
    runtime_specific_args: List[str],
    in_container: bool,
    expected_frame: str,
) -> None:
    run_gprofiler_in_container_for_one_session(
        docker_client,
        gprofiler_docker_image,
        output_directory,
        output_collapsed,
        runtime_specific_args,
        profiler_flags + ["--mode=allocation"],
    )

    collapsed_text = Path(output_collapsed).read_text()
    print(collapsed_text)
    assert load_metadata(collapsed_text)["profiling_mode"] == "allocation"

    collapsed = parse_one_collapsed(collapsed_text)
    assert_function_in_collapsed(expected_frame, collapsed)
