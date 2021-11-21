#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

git clone --depth 1 -b kernelheaders-less https://github.com/Granulate/bcc.git && cd bcc && git reset --hard c4cf9f5d8fa1002662ae691afe14dfafbf1800d8

# (after clone, because we copy the licenses)
# TODO support aarch64
if [ $(uname -m) != "x86_64" ]; then
    mkdir -p /bcc/root/share/bcc/examples/cpp/
    touch /bcc/root/share/bcc/examples/cpp/PyPerf
    exit 0
fi

mkdir build
cd build
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
