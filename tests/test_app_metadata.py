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
import platform
from pathlib import Path
from typing import Dict, List

import pytest
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from gprofiler.utils.collapsed_format import parse_one_collapsed
from tests.conftest import AssertInCollapsed
from tests.utils import assert_jvm_flags_equal, is_aarch64, load_metadata, run_gprofiler_in_container_for_one_session


@pytest.mark.parametrize(
    "in_container,runtime,profiler_type,application_docker_command,expected_metadata",
    [
        (
            True,
            "python",
            "pyperf",
            None,
            {
                "exe": "/usr/local/bin/python3.6",
                "execfn": "/usr/local/bin/python",
                "libpython_elfid": (
                    "buildid:64b7f8a37ff81f574936de12c263aade340ed3db"
                    if is_aarch64()
                    else "buildid:0ef3fce0ef90d8f40ad9236793d30081001ee898"
                ),
                "exe_elfid": (
                    "buildid:d627b889c0ac0642ea715651ebb7436ce1ee7444"
                    if is_aarch64()
                    else "buildid:a04b9016e15a247fbc21c91260c13e17a458ed33"
                ),
                "python_version": "Python 3.6.15",
                "sys_maxunicode": None,
                "arch": platform.machine(),
            },
        ),
        (
            True,
            "ruby",
            "rbspy",
            None,
            {
                "exe": "/usr/local/bin/ruby",
                "execfn": "/usr/local/bin/ruby",
                "libruby_elfid": (
                    "buildid:3dd53a0b231fb14f1aaa81e10be000c58a09ee45"
                    if is_aarch64()
                    else "buildid:bf7da94bfdf3cb595ae0af450112076bdaaabee8"
                ),
                "exe_elfid": (
                    "buildid:8a28e8baf87a769f077bf28c053811ce4ffbebed"
                    if is_aarch64()
                    else "buildid:cbc0ab21749fe48b904fff4e73b88413270bd8ba"
                ),
                "ruby_version": (
                    "ruby 2.6.7p197 (2021-04-05 revision 67941) [aarch64-linux]"
                    if is_aarch64()
                    else "ruby 2.6.7p197 (2021-04-05 revision 67941) [x86_64-linux]"
                ),
                "arch": platform.machine(),
            },
        ),
        (
            True,
            "java",
            "ap",
            None,
            {
                "exe": "/usr/local/openjdk-8/bin/java",
                "execfn": "/usr/local/openjdk-8/bin/java",
                "java_version": 'openjdk version "1.8.0_322"\n'
                "OpenJDK Runtime Environment (build 1.8.0_322-b06)\n"
                "OpenJDK 64-Bit Server VM (build 25.322-b06, mixed mode)",
                "libjvm_elfid": (
                    "buildid:33a1021cade63f16e30726be4111f20c34444764"
                    if is_aarch64()
                    else "buildid:622795512a2c037aec4d7ca6da05527dae86e460"
                ),
                "arch": platform.machine(),
                "jvm_flags": [
                    {
                        "name": "CICompilerCount",
                        "type": "intx",
                        "value": None,
                        "origin": "non-default",
                        "kind": ["product"],
                    },
                    {
                        "name": "InitialHeapSize",
                        "type": "uintx",
                        "value": None,
                        "origin": "non-default",
                        "kind": ["product"],
                    },
                    {
                        "name": "MaxHeapSize",
                        "type": "uintx",
                        "value": None,
                        "origin": "non-default",
                        "kind": ["product"],
                    },
                    {
                        "name": "MaxNewSize",
                        "type": "uintx",
                        "value": None,
                        "origin": "non-default",
                        "kind": ["product"],
                    },
                    {
                        "name": "MinHeapDeltaBytes",
                        "type": "uintx",
                        "value": None,
                        "origin": "non-default",
                        "kind": ["product"],
                    },
                    {
                        "name": "NewSize",
                        "type": "uintx",
                        "value": None,
                        "origin": "non-default",
                        "kind": ["product"],
                    },
                    {
                        "name": "OldSize",
                        "type": "uintx",
                        "value": None,
                        "origin": "non-default",
                        "kind": ["product"],
                    },
                    {
                        "name": "UseCompressedClassPointers",
                        "type": "bool",
                        "value": None,
                        "origin": "non-default",
                        "kind": ["lp64_product"],
                    },
                    {
                        "name": "UseCompressedOops",
                        "type": "bool",
                        "value": None,
                        "origin": "non-default",
                        "kind": ["lp64_product"],
                    },
                    {
                        "name": "UseParallelGC",
                        "type": "bool",
                        "value": None,
                        "origin": "non-default",
                        "kind": ["product"],
                    },
                ],
            },
        ),
        (
            True,
            "golang",
            "perf",
            ["./fibonacci"],
            {
                "exe": "/app/fibonacci",
                "execfn": "./fibonacci",
                "golang_version": "go1.18.3",
                "link": "dynamic",
                "libc": "glibc",
                "stripped": False,
                "arch": platform.machine(),
            },
        ),
        (
            True,
            "golang",
            "perf",
            ["./fibonacci-stripped"],
            {
                "exe": "/app/fibonacci-stripped",
                "execfn": "./fibonacci-stripped",
                "golang_version": None,
                "link": "dynamic",
                "libc": "glibc",
                "stripped": True,
                "arch": platform.machine(),
            },
        ),
        (
            True,
            "nodejs",
            "perf",
            None,
            {
                "exe": "/usr/local/bin/node",
                "execfn": "/usr/local/bin/node",
                "node_version": "v10.24.1",
                "link": "dynamic",
                "libc": "glibc",
                "arch": platform.machine(),
            },
        ),
        (
            True,
            "dotnet",
            "dotnet-trace",
            None,
            {
                "dotnet_version": "6.0.302",
                "exe": "/usr/share/dotnet/dotnet",
                "execfn": "/usr/bin/dotnet",
                "arch": platform.machine(),
            },
        ),
    ],
)
def test_app_metadata(
    docker_client: DockerClient,
    application_docker_container: Container,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    assert_collapsed: AssertInCollapsed,
    profiler_flags: List[str],
    runtime: str,
    profiler_type: str,
    expected_metadata: Dict,
    application_executable: str,
) -> None:
    if runtime == "dotnet":
        pytest.xfail("Dotnet-trace doesn't work with alpine: https://github.com/Granulate/gprofiler/issues/795")
    if profiler_type == "pyperf" and is_aarch64():
        pytest.xfail("PyPerf doesn't run on Aarch64 - https://github.com/Granulate/gprofiler/issues/499")
    run_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, output_directory, output_collapsed, runtime_specific_args, profiler_flags
    )
    collapsed_text = Path(output_collapsed).read_text()
    # sanity
    collapsed = parse_one_collapsed(collapsed_text)
    assert_collapsed(collapsed)

    metadata = load_metadata(collapsed_text)

    assert application_docker_container.name in metadata["containers"]
    # find its app metadata index - find a stack line from the app of this container
    stack = next(
        filter(
            lambda line: application_docker_container.name in line and application_executable in line,
            collapsed_text.splitlines()[1:],
        )
    )
    # stack begins with index
    idx = int(stack.split(";")[0])

    if runtime == "java":
        # don't check JVM flags in direct comparison, as they might change a bit across machines due to ergonomics
        actual_jvm_flags = metadata["application_metadata"][idx].pop("jvm_flags")
        expected_jvm_flags = expected_metadata.pop("jvm_flags")
        assert_jvm_flags_equal(actual_jvm_flags=actual_jvm_flags, expected_jvm_flags=expected_jvm_flags)

    # values from the current test container
    assert metadata["application_metadata"][idx] == expected_metadata
