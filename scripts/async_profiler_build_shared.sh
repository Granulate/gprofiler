#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=v2.9g2
GIT_REV="230eb3b8608be625b264a35dd45846c62b47b313"

git clone --depth 1 -b "$VERSION" https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
