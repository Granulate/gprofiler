#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

DETECTED_PYTHON_PROCESSES_REGEX = r"(?:^.+/(?:lib)?python[^/]*$)|(?:^.+/site-packages/.+?$)|(?:^.+/dist-packages/.+?$)"

_BLACKLISTED_PYTHON_PROCS = ["unattended-upgrades", "networkd-dispatcher", "supervisord", "tuned"]
