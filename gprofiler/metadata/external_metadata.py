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

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from gprofiler.log import get_logger_adapter
from gprofiler.metadata import ProfileMetadata

PidToAppMetadata = Dict[int, ProfileMetadata]

logger = get_logger_adapter(__name__)


EXTERNAL_METADATA_STALENESS_THRESHOLD_S = 5 * 60  # 5 minutes


class ExternalMetadataStaleError(Exception):
    pass


@dataclass
class ExternalMetadata:
    static: ProfileMetadata
    application: PidToAppMetadata


def read_external_metadata(external_metadata_path: Optional[Path]) -> ExternalMetadata:
    """
    If external metadata is given, read it.
    External metadata has a simple format:
    {
        "static": {
            "key1": "value1",
            "key2": "value2"
        }
        "application": {
            "pid1": {
                "key1": "value1",
            },
            "pid2": {
                "key1": "value1",
            }
        }
    }

    "static" data is attached to the static metadata that gProfiler sends. It is read once by gProfiler
    upon starting.
    "application" metadata is attached to the application metadata that gProfiler sends per PID. The external metadata
    file is re-read every profiling session to update the application metadata.
    """
    if external_metadata_path is None:
        return ExternalMetadata({}, {})

    last_update = external_metadata_path.stat().st_mtime
    if time.time() - last_update > EXTERNAL_METADATA_STALENESS_THRESHOLD_S:
        raise ExternalMetadataStaleError(
            f"External metadata is stale {external_metadata_path} last update at ts {last_update}"
        )

    try:
        external_metadata = json.loads(external_metadata_path.read_text())
        # PID keys are strings in the JSON, but we want them to be ints.
        application = {int(k): v for k, v in external_metadata.get("application", {}).items()}
        return ExternalMetadata(external_metadata.get("static", {}), application)
    except Exception:
        logger.exception("Failed to read external metadata", external_metadata_path=str(external_metadata_path))
        return ExternalMetadata({}, {})
