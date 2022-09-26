#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import urllib.request
from typing import Optional, cast


def get_https_proxy() -> Optional[str]:
    """
    We follow what requests uses.
    """
    return cast(Optional[str], urllib.request.getproxies_environment().get("https"))
