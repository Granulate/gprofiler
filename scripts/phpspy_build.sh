#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# TODO support aarch64
if [ $(uname -m) != "x86_64" ]; then
    mkdir -p /phpspy
    touch /phpspy/phpspy
    mkdir -p /binutils/binutils-2.25/bin/bin/
    touch /binutils/binutils-2.25/bin/bin/objdump
    exit 0
fi

# First build phpspy
pushd /
git clone --depth=1 -b v0.6.0-g4 --recursive https://github.com/Granulate/phpspy.git && git -C phpspy reset --hard af1d74ebd4f9c27486e82397eafde1c450f06510
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
./configure --disable-nls --prefix=$(pwd)/bin --disable-ld --disable-gdb
make configure-host
make LDFLAGS="-all-static"
make install
popd
