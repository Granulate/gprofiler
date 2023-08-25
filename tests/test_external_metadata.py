#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import json
from pathlib import Path
from typing import List, cast

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
    application_pid_str = str(application_pid)
    external_metadata = {
        "static": {
            "value1": 555,
            "value2": "string",
        },
        "application": {
            application_pid_str: {
                "value3": "1234",
                "value4": False,
            }
        },
    }
    output_directory.mkdir(parents=True, exist_ok=True)
    Path(output_directory / "external_metadata.json").write_text(json.dumps(external_metadata))

    profiler_flags.extend(["--pids", application_pid_str])
    # TODO pass the path properly
    profiler_flags.extend(["--external-metadata", "/tmp/gprofiler/external_metadata.json"])
    run_gprofiler_in_container_for_one_session(
        docker_client, gprofiler_docker_image, output_directory, output_collapsed, [], profiler_flags
    )
    collapsed_text = Path(output_collapsed).read_text()
    metadata = load_metadata(collapsed_text)

    assert metadata["metadata"]["external_metadata"] == external_metadata["static"]

    # we profiled only the application PID, so we expect 2 app metadatas - the null one and ours.
    app_metadata = metadata["application_metadata"]
    assert len(app_metadata) == 2
    assert app_metadata[0] is None  # null metadata
    # app external metadata is contained in the application metadata.
    assert cast(dict, external_metadata["application"])[application_pid_str].items() <= app_metadata[1].items()
