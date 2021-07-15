#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

git clone --depth 6 -b feature/show-native-stacks https://github.com/Granulate/bcc.git && cd bcc && git reset --hard 213f36888ecee09df71a257402dc2a7e35c95e09
mkdir build
cd build
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
