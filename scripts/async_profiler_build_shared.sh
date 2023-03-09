#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=add_method_modifiers
GIT_REV="3b60db24f5825a55f7ad9dd0aa3487049ad3bb77"

git clone --depth 1 -b "$VERSION" https://github.com/mpozniak95/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"

make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
