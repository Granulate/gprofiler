#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v2.0g2 https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard 9692c57ab9b3f77cd489a6ee26cad18d081c3e45
make release
