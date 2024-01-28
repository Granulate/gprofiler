#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=v3.0g1
GIT_REV="952b30f9d28aeb9cb9e418ed6f60772dfdf83e46"

git clone --depth 1 -b "$VERSION" https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
