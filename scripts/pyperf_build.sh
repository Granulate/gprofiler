#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b pyperf-libunwind-accessors https://github.com/Granulate/bcc.git && cd bcc && git reset --hard 72375d48b2b743dbed399b7ed2e2be1b2a6edf74

# (after clone, because we copy the licenses)
# TODO support aarch64
if [ "$(uname -m)" != "x86_64" ]; then
    mkdir -p /bcc/root/share/bcc/examples/cpp/
    touch /bcc/root/share/bcc/examples/cpp/PyPerf
    exit 0
fi

mkdir build
cd build
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
