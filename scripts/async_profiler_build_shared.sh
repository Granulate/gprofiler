#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=v2.10g1
GIT_REV="6c161dc29c46e8dcc89eb5b0dffd5f460febd139"

git clone --depth 1 https://github.com/mpozniak95/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
