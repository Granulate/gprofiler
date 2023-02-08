#!/usr/bin/env sh
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -eu

LIBUNWIND_VERSION=1.6.2
LIBUNWIND_TAR=libunwind-"$LIBUNWIND_VERSION".tar.gz

curl -L https://github.com/libunwind/libunwind/releases/download/v"$LIBUNWIND_VERSION"/"$LIBUNWIND_TAR" -o "$LIBUNWIND_TAR"
tar -xf "$LIBUNWIND_TAR"
cd libunwind-"$LIBUNWIND_VERSION"

if [ "$(uname -m)" = "aarch64" ]; then
    # higher value for make -j kills the GH runner (build gets OOM)
    nproc=2
else
    nproc=$(nproc)
fi

prefix_flag=""
if [ "$#" -gt 2 ]; then
    echo "Too many arguments"
elif [ "$#" -eq 1 ]; then
    prefix_flag="--prefix=$1"
fi

./configure --disable-tests --disable-documentation "$prefix_flag" && make install -j "$nproc"
cd ..
rm -r libunwind-"$LIBUNWIND_VERSION"
rm "$LIBUNWIND_TAR"
