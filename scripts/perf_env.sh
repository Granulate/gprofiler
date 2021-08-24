#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

apt-get update
apt-get install -y --no-install-recommends \
    gcc \
    g++ \
    make \
    git \
    curl \
    ca-certificates \
    lbzip2 \
    unzip \
    patch \
    python3 \
    autoconf \
    libssl-dev \
    zlib1g-dev \
    libaudit-dev \
    binutils-dev \
    libiberty-dev \
    libcap-dev \
    libdwarf-dev \
    liblzma-dev \
    libnuma-dev \
    libbabeltrace-ctf-dev \
    systemtap-sdt-dev \
    libslang2-dev \
    libzstd-dev \
    libbz2-dev \
    flex \
    bison

cd /tmp

# install newer versions of elfutils
curl -L ftp://sourceware.org/pub/elfutils/0.179/elfutils-0.179.tar.bz2 -o elfutils-0.179.tar.bz2
tar -xf elfutils-0.179.tar.bz2
pushd elfutils-0.179
./configure --disable-debuginfod --prefix=/usr && make -j 8 && make install
popd
rm -r elfutils-0.179
rm elfutils-0.179.tar.bz2
