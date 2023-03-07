#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=add_method_modifiers
GIT_REV="b693b5a726b3919959cf62d6f1982c972e07c66e"

git clone --depth 1 -b "$VERSION" https://github.com/mpozniak95/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
