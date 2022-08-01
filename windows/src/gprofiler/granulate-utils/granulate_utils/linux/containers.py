#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import re
from typing import Optional, Union

from psutil import Process

from granulate_utils.linux.cgroups import get_cgroups

# ECS uses /ecs/uuid/container-id
# standard Docker uses /docker/container-id
# k8s uses /kubepods/{burstable,besteffort}/uuid/container-id
# there are some variations to the above formats, but generally, the container
# ID is always 64-hex digits.
CONTAINER_ID_PATTERN = re.compile(r"[a-f0-9]{64}")


def get_process_container_id(process: Union[int, Process]) -> Optional[str]:
    """
    Gets the container ID of a running process, or None if not in a container.
    :raises NoSuchProcess: If the process doesn't or no longer exists
    """
    pid = process if isinstance(process, int) else process.pid
    for _, _, cgpath in get_cgroups(pid):
        found = CONTAINER_ID_PATTERN.findall(cgpath)
        if found:
            return found[-1]

    return None
