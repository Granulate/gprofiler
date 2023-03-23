#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import resource


def disable_core_files() -> None:
    """
    Prevents core files from being generated for processes executed by us.
    """
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
