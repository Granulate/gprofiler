#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from pathlib import Path

HERE = Path(__file__).parent
PARENT = HERE.parent
CONTAINERS_DIRECTORY = HERE / "containers"
RESOURCES_DIRECTORY = PARENT / "gprofiler" / "resources"

PHPSPY_DURATION = 3  # seconds
