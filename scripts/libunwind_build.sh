#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

curl -L https://download.savannah.nongnu.org/releases/libunwind/libunwind-1.5.0.tar.gz -o libunwind-1.5.0.tar.gz
tar -xf libunwind-1.5.0.tar.gz
# Add containers support in libunwind
curl https://github.com/libunwind/libunwind/commit/831459ee961e7d673bbd83e40d0823227c66db33.patch | sed s/unw_ltoa/ltoa/g > libunwind-container-support.patch
pushd libunwind-1.5.0
patch -p1 < ../libunwind-container-support.patch

if [ "$(uname -m)" = "aarch64" ]; then
    # higher value for make -j kills the GH runner (build gets OOM)
    nproc=2
else
    nproc=$(nproc)
fi

./configure --prefix=/usr --disable-tests --disable-documentation && make install -j "$nproc"
popd
rm -r libunwind-1.5.0
rm libunwind-1.5.0.tar.gz
