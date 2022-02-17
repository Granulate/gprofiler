#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from typing import Dict, Optional, Union

from psutil import Process

from gprofiler.utils.elf import get_elf_buildid


def get_application_metadata(process: Union[int, Process]) -> Optional[Dict]:
    pid = process if isinstance(process, int) else process.pid
    try:
        buildid = get_elf_buildid(f"/proc/{pid}/exe") if pid != 0 else None
    except FileNotFoundError:
        buildid = None
    return {"build_id": buildid}
