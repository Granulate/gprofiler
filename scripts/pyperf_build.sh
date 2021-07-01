#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

git clone --shallow-since="Thu Jul 1 12:34:38 2021 +0000" --single-branch -b feature/show-native-stacks https://github.com/Granulate/bcc.git && cd bcc && git reset --hard cfbebc7fd723943d8cad4a23c76af3332dd2d88c
mkdir build
cd build
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
