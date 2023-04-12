#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import json
from pathlib import Path
from typing import Dict, List

import pytest
from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image

from gprofiler.utils.collapsed_format import parse_one_collapsed
from tests.conftest import AssertInCollapsed
from tests.utils import assert_jvm_flags_equal, is_aarch64, run_gprofiler_in_container_for_one_session


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
                "libpython_elfid": "buildid:64b7f8a37ff81f574936de12c263aade340ed3db"
                if is_aarch64()
                else "buildid:0ef3fce0ef90d8f40ad9236793d30081001ee898",
                "exe_elfid": "buildid:d627b889c0ac0642ea715651ebb7436ce1ee7444"
                if is_aarch64()
                else "buildid:a04b9016e15a247fbc21c91260c13e17a458ed33",
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
                "libruby_elfid": "buildid:3dd53a0b231fb14f1aaa81e10be000c58a09ee45"
                if is_aarch64()
                else "buildid:bf7da94bfdf3cb595ae0af450112076bdaaabee8",
                "exe_elfid": "buildid:8a28e8baf87a769f077bf28c053811ce4ffbebed"
                if is_aarch64()
                else "buildid:cbc0ab21749fe48b904fff4e73b88413270bd8ba",
                "ruby_version": "ruby 2.6.7p197 (2021-04-05 revision 67941) [aarch64-linux]"
                if is_aarch64()
                else "ruby 2.6.7p197 (2021-04-05 revision 67941) [x86_64-linux]",
            },
        ),
        (
            True,
            "java",
            "ap",
            {
                "exe": "/usr/local/openjdk-8/bin/java",
                "execfn": "/usr/local/openjdk-8/bin/java",
                "java_version": 'openjdk version "1.8.0_322"\n'
                "OpenJDK Runtime Environment (build 1.8.0_322-b06)\n"
                "OpenJDK 64-Bit Server VM (build 25.322-b06, mixed mode)",
                "libjvm_elfid": "buildid:33a1021cade63f16e30726be4111f20c34444764"
                if is_aarch64()
                else "buildid:622795512a2c037aec4d7ca6da05527dae86e460",
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
    docker_client: DockerClient,
    application_docker_container: Container,
    runtime_specific_args: List[str],
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    assert_collapsed: AssertInCollapsed,
    profiler_flags: List[str],
    runtime: str,
    expected_metadata: Dict,
    application_executable: str,
) -> None:
    run_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, output_directory, output_collapsed, runtime_specific_args, profiler_flags
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
    stack = next(
        filter(lambda line: application_docker_container.name in line and application_executable in line, lines[1:])
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
