#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b aarch64 https://github.com/Granulate/bcc.git && cd bcc && git reset --hard b2c69412b3abaaf2bef02ff95459f4572574431d

mkdir build
cd build

SHARED_ARG=""
# need in aarch64 as mentioned here: https://github.com/iovisor/bcc/issues/3333#issuecomment-803432248
# container mdoe doesn't want it - we don't have the libs bundled.
# exe mode in x86_64 works fine so I don't change it.
if [ $(uname -m) = "aarch64" ] && [ "$1" == "exe" ]; then
    SHARED_ARG=" -DENABLE_LLVM_SHARED=1"
fi
cmake -DPYTHON_CMD=python3 -DINSTALL_CPP_EXAMPLES=y -DCMAKE_INSTALL_PREFIX=/bcc/root $SHARED_ARG ..
make -C examples/cpp/pyperf -j -l VERBOSE=1 install
