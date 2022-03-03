#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import os
import shutil


def atomic_copy(src: str, dst: str) -> None:
    dst_tmp = f"{dst}.tmp"
    shutil.copy(src, dst)
    os.rename(dst_tmp, dst)
