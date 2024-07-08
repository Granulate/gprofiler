#!/usr/bin/env bash
#
# Copyright (C) 2022 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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

git clone --depth 1 -b v1.5.0 https://github.com/Granulate/bcc.git && cd bcc && git reset --hard 928423128e10020934df1f7b4641e56b502c2946

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
