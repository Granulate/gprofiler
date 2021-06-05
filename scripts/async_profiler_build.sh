#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v2.0g3 https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard 51447a849d686e899c1cd393e83f0f7c41685d95
make release
