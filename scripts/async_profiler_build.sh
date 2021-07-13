#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v2.0g4 https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard 9d29169e34abc004f534a85ba6a4cf8920250381
make release
