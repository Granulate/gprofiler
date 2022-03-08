#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import os
import shutil


def safe_copy(src: str, dst: str) -> None:
    """
    Safely copies 'src' to 'dst'. Safely means that writing 'dst' is performed at a temporary location,
    and the file is then moved, making the filesystem-level change atomic.
    """
    dst_tmp = f"{dst}.tmp"
    shutil.copy(src, dst_tmp)
    os.rename(dst_tmp, dst)
