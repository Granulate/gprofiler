#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=v2.8.3g3
GIT_REV="008a8cf435bedce8eec3ef36fdc67e742b070566"

git clone --depth 1 -b "$VERSION" https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard "$GIT_REV"
source "$1"
make all

# add a version file to the build directory
echo -n "$VERSION" > build/async-profiler-version

# build verifications regarding libstdc++.
if ldd /bin/ls | grep -q musl ; then
    # ensure no libstdc++
    if ldd build/libasyncProfiler.so | grep -q "libstdc++"; then
        echo "libstdc++ found!"
        ldd build/libasyncProfiler.so
        exit 1
    fi
else
    # ensure libstdc++
    if ! ldd build/libasyncProfiler.so | grep -q "libstdc++"; then
        echo "libstdc++ not found!"
        ldd build/libasyncProfiler.so
        exit 1
    fi
fi
