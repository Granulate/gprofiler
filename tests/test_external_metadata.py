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
import json
import os
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
    external_metadata_filename = "external_metadata.json"
    Path(output_directory / external_metadata_filename).write_text(json.dumps(external_metadata))

    profiler_flags.extend(["--pids", application_pid_str])
    inner_output_directory = "/tmp/gprofiler"
    profiler_flags.extend(["--external-metadata", os.path.join(inner_output_directory, external_metadata_filename)])
    run_gprofiler_in_container_for_one_session(
        docker_client,
        gprofiler_docker_image,
        output_directory,
        output_collapsed,
        [],
        profiler_flags,
        inner_output_directory=inner_output_directory,
    )
    collapsed_text = Path(output_collapsed).read_text()
    collapsed_metadata = load_metadata(collapsed_text)

    assert collapsed_metadata["metadata"]["external_metadata"] == external_metadata["static"]

    # we profiled only the application PID, so we expect 2 app metadatas - the null one and ours.
    app_metadata = collapsed_metadata["application_metadata"]
    assert len(app_metadata) == 2
    assert app_metadata[0] is None  # null metadata
    # app external metadata is contained in the application metadata.
    assert cast(dict, external_metadata["application"])[application_pid_str].items() <= app_metadata[1].items()
