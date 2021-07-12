#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b static-libstdcpp https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard 6483566ab9560e882c29e08cea92a37fae4cf77e
make release
