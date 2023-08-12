#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

from gprofiler.metadata import Metadata

PidToAppMetadata = Dict[int, Metadata]


@dataclass
class ExternalMetadata:
    static: Metadata
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

    external_metadata = json.loads(external_metadata_path.read_text())
    return ExternalMetadata(external_metadata.get("static", {}), external_metadata.get("application", {}))
