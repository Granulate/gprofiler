#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from typing import Optional, cast
from urllib.request import getproxies_environment  # type: ignore  # incorrectly yells at it


def get_https_proxy() -> Optional[str]:
    """
    We follow what requests uses.
    """
    return cast(Optional[str], getproxies_environment().get("https"))
