#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

curl -L http://download.savannah.nongnu.org/releases/libunwind/libunwind-1.5.0.tar.gz -o libunwind-1.5.0.tar.gz
tar -xf libunwind-1.5.0.tar.gz
pushd libunwind-1.5.0
patch -p1 < ../libunwind-container-support.patch
./configure --prefix=/usr --disable-tests --disable-documentation && make install
popd
rm -r libunwind-1.5.0
rm libunwind-1.5.0.tar.gz
