#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=v2.10g1
GIT_REV="b3baf6010e31378679a28fe1cd30168f51cc1ade"

git clone --depth 1 -b "$VERSION" https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
