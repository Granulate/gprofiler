#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=apply-common-format-for-DSO-names
GIT_REV="c1ef61b393cd83cf0763df9aff80ae5e2097bd44"

git clone --depth 1 -b "$VERSION" https://github.com/marcin-ol/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
# shellcheck disable=SC1090  # we pass it either async_profiler_build_glibc.sh or async_profiler_build_musl.sh
source "$1"
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version
