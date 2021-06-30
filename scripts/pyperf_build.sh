#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

git clone --shallow-since="Tue Jun 29 02:14:37 2021 +0000" --single-branch -b feature/show-native-stacks https://github.com/Granulate/bcc.git && cd bcc && git reset --hard b3335b39ed1f28f5dd8b3c325cf94e2f87f0906b
mkdir build
cd build
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
