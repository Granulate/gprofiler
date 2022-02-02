#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

git clone --depth 1 -b aarch64 https://github.com/Granulate/bcc.git && cd bcc && git reset --hard aba6bdc0dcf089128b5d74f4b30ee0d86b56567b

mkdir build
cd build
# TODO -DENABLE_LLVM_SHARED only for aarch exe
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root -DENABLE_LLVM_SHARED=1 ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
