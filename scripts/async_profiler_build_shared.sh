#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=add_method_modifiers
GIT_REV="664d04f18da2205b4f34ce422ec144dd2974906e"

git clone --depth 1 -b "$VERSION" https://github.com/mpozniak95/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"

make all CXXFLAGS="-O3 -fno-omit-frame-pointer -fvisibility=hidden -std=c++11"

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
