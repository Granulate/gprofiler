#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=v2.7g1
GIT_REV="3d5777b3705c7ef58950e99232a3691916d90df6"

git clone --depth 1 -b "$VERSION" https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
source "$1"
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
