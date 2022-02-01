#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

git clone --depth 1 -b aarch64 https://github.com/Granulate/bcc.git && cd bcc && git reset --hard 1d502e0ffb93bbfec6d77dddeb7668f0a90e6810

mkdir build
cd build
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root -DENABLE_LLVM_SHARED=1 ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
