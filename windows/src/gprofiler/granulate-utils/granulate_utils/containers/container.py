#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass
class Container:
    """
    Shared "Container" descriptor class, used for Docker containers & CRI containers.
    """

    runtime: str  # docker / containerd / crio
    # container name for Docker
    # reconstructed container name (as if it were Docker) for CRI
    name: str
    id: str
    labels: Dict[str, str]
    running: bool
    # None if not requested / container is dead
    pid: Optional[int]


class ContainersClientInterface:
    def list_containers(self, all_info: bool) -> List[Container]:
        raise NotImplementedError

    def get_container(self, container_id: str, all_info: bool) -> Container:
        raise NotImplementedError

    def get_runtimes(self) -> List[str]:
        raise NotImplementedError
