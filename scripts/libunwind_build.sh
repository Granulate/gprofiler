#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

curl -L https://download.savannah.nongnu.org/releases/libunwind/libunwind-1.6.2.tar.gz -o libunwind-1.6.2.tar.gz
tar -xf libunwind-1.6.2.tar.gz
pushd libunwind-1.6.2

if [ "$(uname -m)" = "aarch64" ]; then
    # higher value for make -j kills the GH runner (build gets OOM)
    nproc=2
else
    nproc=$(nproc)
fi

./configure --prefix=/usr --disable-tests --disable-documentation && make install -j "$nproc"
popd
rm -r libunwind-1.6.2
rm libunwind-1.6.2.tar.gz
