#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=tryout-asprof-jattach-passthru
GIT_REV="dbc591843a9b8cc0c847583ff826140d3b09e484"

git clone --depth 1 -b "$VERSION" https://github.com/marcin-ol/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
