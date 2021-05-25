#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# First build phpspy
pushd /
git clone --depth=1 -b v0.6.0-g3 --recursive https://github.com/Granulate/phpspy.git && git -C phpspy reset --hard fe361c557c648616c644c4968e4efe485753f3e7
cd /phpspy
make
popd

# Then, build binutils - required for static objdump

BINUTILS_VERSION=2.25

mkdir -p /binutils
pushd /binutils
wget http://ftp.gnu.org/gnu/binutils/binutils-$BINUTILS_VERSION.tar.gz
tar xzf binutils-$BINUTILS_VERSION.tar.gz
cd binutils-$BINUTILS_VERSION
./configure --disable-nls --prefix=$(pwd)/bin
make configure-host
make LDFLAGS="-all-static"
make install
popd
