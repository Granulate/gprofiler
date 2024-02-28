#!/usr/bin/env bash
#
# Copyright (C) 2023 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
ZSTD_VERSION=1.5.2
curl -L https://github.com/facebook/zstd/releases/download/v$ZSTD_VERSION/zstd-$ZSTD_VERSION.tar.gz -o zstd-$ZSTD_VERSION.tar.gz
tar -xf zstd-$ZSTD_VERSION.tar.gz
pushd zstd-$ZSTD_VERSION
make -j && make install
popd
rm -r zstd-$ZSTD_VERSION zstd-$ZSTD_VERSION.tar.gz

# install newer versions of elfutils
ELFUTILS_VERSION=0.187
curl --retry-delay 5 --retry 5 -L https://sourceware.org/elfutils/ftp/$ELFUTILS_VERSION/elfutils-$ELFUTILS_VERSION.tar.bz2 -o elfutils-$ELFUTILS_VERSION.tar.bz2  # sourceware is flaky, so we have some retries
tar -xf elfutils-$ELFUTILS_VERSION.tar.bz2
pushd elfutils-$ELFUTILS_VERSION
# disable debuginfod, otherwise it will try to dlopen("libdebuginfod.so") in runtime & that can
# cause problems, see https://github.com/Granulate/gprofiler/issues/340.
./configure --disable-debuginfod --disable-libdebuginfod --prefix=/usr && make -j && make install
popd
rm -r elfutils-$ELFUTILS_VERSION elfutils-$ELFUTILS_VERSION.tar.bz2
