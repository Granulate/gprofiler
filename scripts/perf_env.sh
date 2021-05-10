#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

apt-get update && apt-get install -y \
    build-essential \
    git \
    curl \
    autoconf \
    asciidoc \
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
    libslang2-dev \
    libzstd-dev \
    libbz2-dev \
    flex \
    bison ;

# install newer versions of elfutils
curl -L ftp://sourceware.org/pub/elfutils/0.183/elfutils-0.183.tar.bz2 -o /tmp/elfutils-0.183.tar.bz2
cd /tmp && tar -xf elfutils-0.183.tar.bz2
cd /tmp/elfutils-0.183 && ./configure --disable-debuginfod --prefix=/usr && make && make install

# & libunwind
curl -L http://download.savannah.nongnu.org/releases/libunwind/libunwind-1.5.0.tar.gz -o /tmp/libunwind-1.5.0.tar.gz
cd /tmp && tar -xf libunwind-1.5.0.tar.gz
cd /tmp/libunwind-1.5.0 && ./configure --prefix=/usr && make install
