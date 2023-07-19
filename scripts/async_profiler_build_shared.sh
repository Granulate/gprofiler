#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=timeout-recycle
GIT_REV="46a6883d084e443a0b2a91ba9208302e65fe216a"

git clone --depth 1 -b "$VERSION" https://github.com/marcin-ol/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
