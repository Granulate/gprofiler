#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# prepares the environment for building py-spy:
# 1. installs the rust target $(uname -m)-unknown-linux-musl - this can build static binaries
# 2. downloads, builds & installs libunwind with musl
# 2. downloads, builds & installs libz with musl
# I use musl because it builds statically. otherwise, we need to build with old glibc; I tried to
# build on centos:7 but it caused some errors out-of-the-box (libunwind was built w/o -fPIC and rust tried
# to build it as shared (?))
# in any way, building it static solves all issues. and I find it better to use more recent versions of libraries
# like libunwind/zlib.

rustup target add "$(uname -m)"-unknown-linux-musl

apt-get update && apt-get install -y musl-dev musl-tools

mkdir builds && cd builds

wget https://github.com/libunwind/libunwind/releases/download/v1.5/libunwind-1.5.0.tar.gz
tar -xf libunwind-1.5.0.tar.gz
pushd libunwind-1.5.0
CC=musl-gcc ./configure --disable-minidebuginfo --enable-ptrace --disable-tests --disable-documentation
make
make install
popd
rm -r libunwind-1.5.0
rm libunwind-1.5.0.tar.gz

ZLIB_VERSION=1.2.12
ZLIB_FILE="zlib-$ZLIB_VERSION.tar.xz"
wget "https://zlib.net/$ZLIB_FILE"
tar -xf "$ZLIB_FILE"
pushd "zlib-$ZLIB_VERSION"
# note the use of --prefix here. it matches the directory https://github.com/benfred/remoteprocess/blob/master/build.rs expects to find libs for musl.
# the libunwind configure may install it in /usr/local/lib for all I care, but if we override /usr/local/lib/libz... with the musl ones,
# it won't do any good...
# --static - we don't need the shared build, we compile everything statically anyway.
CC=musl-gcc ./configure --prefix=/usr/local/musl/"$(uname -m)"-unknown-linux-musl --static
make
make install
popd
rm -fr $ZLIB_FILE zlib-*
