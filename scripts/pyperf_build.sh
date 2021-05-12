#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

git clone --depth 1 -b v1.0.1 https://github.com/Granulate/bcc.git && git -C bcc reset --hard 92b61ade89f554859950695b067288f60cb1f3e5
mkdir bcc/build
cd bcc/build
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
