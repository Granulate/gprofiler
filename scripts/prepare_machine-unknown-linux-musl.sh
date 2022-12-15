#!/usr/bin/env sh
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -eu

# prepares the environment for building py-spy & rbspy:
# 1. installs the rust target $(uname -m)-unknown-linux-musl - this can build static binaries
# 2. downloads, builds & installs libz
# 2. downloads, builds & installs libunwind
# I use musl because it builds statically. otherwise, we need to build with old glibc; I tried to
# build on centos:7 but it caused some errors out-of-the-box (libunwind was built w/o -fPIC and rust tried
# to build it as shared (?))
# in any way, building it static solves all issues. and I find it better to use more recent versions of libraries
# like libunwind/zlib.

target="$(uname -m)"-unknown-linux-musl
target_dir="/usr/local/musl/$target/"  # as searched for by the remoteprocess create.
rustup target add "$target"

apk add --no-cache musl-dev make git curl  # git & curl used by next scripts

mkdir builds && cd builds

ZLIB_VERSION=1.2.13
ZLIB_FILE="zlib-$ZLIB_VERSION.tar.xz"
wget "https://zlib.net/$ZLIB_FILE"
tar -xf "$ZLIB_FILE"
cd "zlib-$ZLIB_VERSION"
# note the use of --prefix here. it matches the directory https://github.com/benfred/remoteprocess/blob/master/build.rs expects to find libs for musl.
# the libunwind configure may install it in /usr/local/lib for all I care, but if we override /usr/local/lib/libz... with the musl ones,
# it won't do any good...
# --static - we don't need the shared build, we compile everything statically anyway.
./configure --static --prefix="$target_dir"
make install
cd ..
rm -fr $ZLIB_FILE zlib-*

/tmp/libunwind_build.sh "$target_dir"
