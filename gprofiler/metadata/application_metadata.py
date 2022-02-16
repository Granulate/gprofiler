#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from typing import Dict, Optional, Union

from psutil import Process


def get_application_metadata(process: Union[int, Process]) -> Optional[Dict]:
    return None
