#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# First build phpspy
pushd /tmp
git clone --depth=1 -b v0.6.0-g4 --recursive https://github.com/Granulate/phpspy.git && git -C phpspy reset --hard af1d74ebd4f9c27486e82397eafde1c450f06510
cd /tmp/phpspy
make
popd

# Then, build binutils - required for static objdump

BINUTILS_VERSION=2.25

mkdir -p /tmp/binutils
pushd /tmp/binutils
wget http://ftp.gnu.org/gnu/binutils/binutils-$BINUTILS_VERSION.tar.gz
tar xzf binutils-$BINUTILS_VERSION.tar.gz
cd binutils-$BINUTILS_VERSION
./configure --disable-nls --prefix="$(pwd)"/bin --disable-ld --disable-gdb
make configure-host
make LDFLAGS="-all-static"
make install
popd
