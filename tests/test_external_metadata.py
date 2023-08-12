#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import json
from pathlib import Path
from typing import List

import pytest
from docker import DockerClient
from docker.models.images import Image

from tests.utils import load_metadata, run_gprofiler_in_container_for_one_session


@pytest.mark.parametrize("in_container", [True])
@pytest.mark.parametrize("runtime,profiler_type", [("java", "ap"), ("golang", "perf")])
def test_external_metadata(
    docker_client: DockerClient,
    gprofiler_docker_image: Image,
    output_directory: Path,
    output_collapsed: Path,
    application_pid: int,
    runtime: str,
    profiler_type: str,
    profiler_flags: List[str],
) -> None:
    external_metadata = {
        "static": {
            "value1": 555,
            "value2": "string",
        },
        "application": {
            application_pid: {
                "value3": "1234",
                "value4": False,
            }
        },
    }
    Path(output_directory / "external_metadata.json").write_text(json.dumps(external_metadata))

    profiler_flags.extend(["--pids", str(application_pid)])
    # TODO pass the path properly
    profiler_flags.extend(["--external-metadata", "/tmp/gprofiler/external_metadata.json"])
    run_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, output_directory, output_collapsed, [], profiler_flags
    )
    collapsed_text = Path(output_collapsed).read_text()
    metadata = load_metadata(collapsed_text)

    assert metadata["external"] == external_metadata["static"]
    # we profiled only the application PID, so we expect 2 app metadatas - the empty one and ours.
    assert len(metadata["application_metadata"]) == 2
    assert metadata["application_metadata"][0] is None
    assert external_metadata["application"][application_pid].items() <= metadata["application_metadata"][1].items()
