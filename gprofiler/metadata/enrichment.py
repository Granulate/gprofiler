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
from dataclasses import dataclass
from typing import List, Optional


@dataclass
class EnrichmentOptions:
    """
    Profile enrichment options.
    """

    # profile protocol version. v1 does not support container_names and application_metadata.
    # None means latest
    profile_api_version: Optional[str]
    container_names: bool  # Include container names for each stack in result profile
    application_identifiers: bool  # Attempt to produce & include appid frames for each stack in result profile
    application_identifier_args_filters: List[str]  # A list of regex filters to add cmdline arguments to the app id
    application_metadata: bool  # Include specialized metadata per application, e.g for Python - the Python version
