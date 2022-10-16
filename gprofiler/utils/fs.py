#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import errno
import os
import shutil
from pathlib import Path
from secrets import token_hex
from threading import Event
from typing import Union, Iterator
from contextlib import contextmanager
from gprofiler.utils.process import run_process
from functools import lru_cache
import importlib_resources


@lru_cache(maxsize=None)
def resource_path(relative_path: str = "") -> str:
    *relative_directory, basename = relative_path.split("/")
    package = ".".join(["gprofiler", "resources"] + relative_directory)
    try:
        with importlib_resources.path(package, basename) as path:
            return str(path)
    except ImportError as e:
        raise Exception(f"Resource {relative_path!r} not found!") from e

def remove_path(path: Union[str, Path], missing_ok: bool = False) -> None:
    # backporting missing_ok, available only from 3.8
    try:
        Path(path).unlink()
    except FileNotFoundError:
        if not missing_ok:
            raise


@contextmanager
def removed_path(path: str) -> Iterator[None]:
    try:
        yield
    finally:
        remove_path(path, missing_ok=True)


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


def wait_for_file_by_prefix(prefix: str, timeout: float, stop_event: Event) -> Path:
    glob_pattern = f"{prefix}*"
    wait_event(timeout, stop_event, lambda: len(glob.glob(glob_pattern)) > 0)

    output_files = glob.glob(glob_pattern)
    # All the snapshot samples should be in one file
    if len(output_files) != 1:
        # this can happen if:
        # * the profiler generating those files is erroneous
        # * the profiler received many signals (and it generated files based on signals)
        # * errors in gProfiler led to previous output fails remain not removed
        # in any case, we remove all old files, and assume the last one (after sorting by timestamp)
        # is the one we want.
        logger.warning(
            f"One output file expected, but found {len(output_files)}."
            f" Removing all and using the last one. {output_files}"
        )
        # timestamp format guarantees alphabetical order == chronological order.
        output_files.sort()
        for f in output_files[:-1]:
            os.unlink(f)
        output_files = output_files[-1:]

    return Path(output_files[0])


def remove_prefix(s: str, prefix: str) -> str:
    # like str.removeprefix of Python 3.9, but this also ensures the prefix exists.
    assert s.startswith(prefix), f"{s} doesn't start with {prefix}"
    return s[len(prefix) :]


def touch_path(path: str, mode: int) -> None:
    Path(path).touch()
    # chmod() afterwards (can't use 'mode' in touch(), because it's affected by umask)
    os.chmod(path, mode)


def random_prefix() -> str:
    return "".join(random.choice(string.ascii_letters) for _ in range(16))
