#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import sys
from functools import lru_cache

WINDOWS_PLATFORM_NAME = "win32"
LINUX_PLATFORM_NAME = "linux"


@lru_cache(maxsize=None)
def is_windows() -> bool:
    return sys.platform == WINDOWS_PLATFORM_NAME


@lru_cache(maxsize=None)
def is_linux() -> bool:
    return sys.platform == LINUX_PLATFORM_NAME
