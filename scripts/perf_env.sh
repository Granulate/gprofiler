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
    libbz2-dev \
    flex \
    bison

cd /tmp

# build & install libzstd (elfutils requires newer versions than available in apt)
curl -L https://github.com/facebook/zstd/releases/download/v1.5.2/zstd-1.5.2.tar.gz -o zstd-1.5.2.tar.gz
tar -xf zstd-1.5.2.tar.gz
pushd zstd-1.5.2
make -j && make install
popd
rm -r zstd-1.5.2 zstd-1.5.2.tar.gz

# install newer versions of elfutils
curl -L ftp://sourceware.org/pub/elfutils/0.187/elfutils-0.187.tar.bz2 -o elfutils-0.187.tar.bz2
tar -xf elfutils-0.187.tar.bz2
pushd elfutils-0.187
# disable debuginfod, otherwise it will try to dlopen("libdebuginfod.so") in runtime & that can
# cause problems, see https://github.com/Granulate/gprofiler/issues/340.
./configure --disable-debuginfod --disable-libdebuginfod --prefix=/usr && make -j 8 && make install
popd
rm -r elfutils-0.187 elfutils-0.187.tar.bz2
