#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import errno
import os
import shutil
from pathlib import Path
from secrets import token_hex

from gprofiler.platform import is_windows
from gprofiler.utils import remove_path, run_process


def safe_copy(src: str, dst: str) -> None:
    """
    Safely copies 'src' to 'dst'. Safely means that writing 'dst' is performed at a temporary location,
    and the file is then moved, making the filesystem-level change atomic.
    """
    dst_tmp = f"{dst}.tmp"
    shutil.copy(src, dst_tmp)
    os.rename(dst_tmp, dst)


def is_rw_exec_dir(path: str) -> bool:
    """
    Is 'path' rw and exec?
    """
    # randomize the name - this function runs concurrently on paths of in same mnt namespace.
    test_script = Path(path) / f"t-{token_hex(10)}.sh"

    # try creating & writing
    try:
        os.makedirs(path, 0o755, exist_ok=True)
        test_script.write_text("#!/bin/sh\nexit 0")
        test_script.chmod(0o755)
    except OSError as e:
        if e.errno == errno.EROFS:
            # ro
            return False
        remove_path(test_script)
        raise

    # try executing
    try:
        run_process([str(test_script)], suppress_log=True)
    except PermissionError:
        # noexec
        return False
    finally:
        test_script.unlink()

    return True


def escape_filename(filename: str) -> str:
    return filename.replace(":", "-" if is_windows() else ":")
