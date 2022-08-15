"""
See section 3.5 in https://www.kernel.org/doc/Documentation/filesystems/proc.txt
"""
from typing import Iterable, List, NamedTuple, Union

from typing_extensions import Literal


class Mount(NamedTuple):
    mount_id: int
    parent_id: int
    device: str
    root: str
    mount_point: str
    mount_options: List[str]
    optional_fields: List[str]
    filesystem_type: str
    mount_source: str
    super_options: List[str]


def iter_mountinfo(pid: Union[int, Literal["self"]] = "self") -> Iterable[Mount]:
    """
    Iterate over mounts in mount namespace of pid.
    """
    with open(f"/proc/{pid}/mountinfo") as f:
        for line in f:
            fields = line.split()
            separator_index = fields.index("-", 6)  # marks the end of optional fields
            filesystem_fields = fields[separator_index + 1 :]
            yield Mount(
                mount_id=int(fields[0]),
                parent_id=int(fields[1]),
                device=fields[2],
                root=fields[3],
                mount_point=fields[4],
                mount_options=fields[5].split(","),
                optional_fields=fields[6:separator_index],
                filesystem_type=filesystem_fields[0],
                mount_source=filesystem_fields[1],
                super_options=filesystem_fields[2].split(","),
            )
