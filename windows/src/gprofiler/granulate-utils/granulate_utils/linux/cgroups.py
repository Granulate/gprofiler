#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from pathlib import Path
from typing import List, Tuple

from psutil import NoSuchProcess


def get_cgroups(pid: int) -> List[Tuple[str, List[str], str]]:
    """
    Get the cgroups of a process in [(hier id., controllers, path)] parsed form.
    """

    def parse_line(line: str) -> Tuple[str, List[str], str]:
        hier_id, controller_list, cgroup_path = line.split(":", maxsplit=2)
        return hier_id, controller_list.split(","), cgroup_path

    try:
        text = Path(f"/proc/{pid}/cgroup").read_text()
    except FileNotFoundError:
        raise NoSuchProcess(pid)
    else:
        return [parse_line(line) for line in text.splitlines()]
