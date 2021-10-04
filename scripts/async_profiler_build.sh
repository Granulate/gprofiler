#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=v2.5g1
GIT_REV="02cb785f7cc0024581a1802126f51ed94d5b0475"

git clone --depth 1 -b "$VERSION" https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
set +eu  # this funny script has errors :shrug:
source scl_source enable devtoolset-7
set -eu
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
