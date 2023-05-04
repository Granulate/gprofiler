#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import errno
import os
import shutil
from tempfile import NamedTemporaryFile

from gprofiler.platform import is_windows
from gprofiler.utils import run_process


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

    # try creating & writing
    try:
        os.makedirs(path, 0o755, exist_ok=True)
        with NamedTemporaryFile(dir=path, suffix=".sh") as test_script:
            with open(test_script.name, "w") as f:
                f.write("#!/bin/sh\nexit 0")
            os.chmod(test_script.name, 0o755)
            test_script.file.close()
            # try executing
            try:
                run_process([str(test_script.name)], suppress_log=True)
            except PermissionError:
                # noexec
                return False
    except OSError as e:
        if e.errno == errno.EROFS:
            # ro
            return False
        raise



    return True


def escape_filename(filename: str) -> str:
    return filename.replace(":", "-" if is_windows() else ":")
