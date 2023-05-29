#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

if [ "$#" -gt 1 ]; then
    echo "Too many arguments"
    exit 1
elif [ "$#" -eq 0 ]; then
    with_staticx=""
elif [ "$1" == "--with-staticx" ]; then
    with_staticx="$1"
else
    echo "Unexpected argument: $1"
    exit 1
fi

# TODO support aarch64
if [ "$(uname -m)" != "x86_64" ]; then
    mkdir -p /bcc/root/share/bcc/examples/cpp/
    touch /bcc/root/share/bcc/examples/cpp/PyPerf
    mkdir -p /bcc/bcc/licenses
    touch /bcc/bcc/LICENSE.txt
    touch /bcc/bcc/NOTICE
    exit 0
fi

git clone --depth 1 -b python-3.11 https://github.com/marcin-ol/bcc.git && cd bcc && git reset --hard 8d1f93b42f34e4b1212574240352cd44a5a1334b
mkdir build
cd build
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root -DBCC_CLOADER_KERNEL_HEADERLESS=1 ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
# leave build directory
cd ..
# leave bcc repository
cd ..

# We're using staticx to build a distribution-independent binary of PyPerf because PyPerf
# can only build with latest llvm (>10), which cannot be obtained on CentOS.
if [ -n "$with_staticx" ]; then
    staticx ./root/share/bcc/examples/cpp/PyPerf ./root/share/bcc/examples/cpp/PyPerf
fi
