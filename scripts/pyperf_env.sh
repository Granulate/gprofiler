#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

if [ "$#" -gt 1 ]; then
    echo "Too many arguments"
    exit 1
elif [ "$#" -eq 0 ]; then
    with_staticx=""
elif [ "$1" == "--with-staticx" ]; then
    with_staticx="$1"
else
    echo "Unexpected argument: $1"
    exit 1
fi

if [ "$(uname -m)" = "aarch64" ]; then
    ./bcc_helpers_build.sh  # it needs to create dummy files
    exit 0;
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    iperf llvm-12-dev \
    clang-12 libclang-12-dev \
    cmake \
    python3 python3-pip \
    flex \
    libfl-dev \
    bison \
    libelf-dev \
    libz-dev \
    liblzma-dev \
    ca-certificates \
    git \
    patchelf scons

if [ -n "$with_staticx" ]; then
    if [ "$(uname -m)" = "aarch64" ]; then
        exit 0;
    fi
    git clone -b v0.13.6 https://github.com/JonathonReinhart/staticx.git
    # We're using staticx to build a distribution-independent binary of PyPerf because PyPerf
    # can only build with latest llvm (>10), which cannot be obtained on CentOS.
    cd staticx
    git reset --hard 819d8eafecbaab3646f70dfb1e3e19f6bbc017f8
    # - apply patch to ensure staticx bootloader propagates dump signal to actual PyPerf binary
    # - apply patch removing calls to getpwnam and getgrnam,
    # to avoid crashing the staticx bootloader on ubuntu:22.04+ and centos:8+
    git apply ../staticx_for_pyperf_patch.diff ../staticx_patch.diff
    python3 -m pip install --no-cache-dir .
    cd ..
    rm -rf staticx
fi

./bcc_helpers_build.sh

apt-get clean
rm -rf /var/lib/apt/lists/*
