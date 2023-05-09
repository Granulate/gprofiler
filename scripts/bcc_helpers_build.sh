#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# TODO support aarch64, after we support it in PyPerf
if [ "$(uname -m)" != "x86_64" ]; then
    mkdir -p /bpf_get_fs_offset /bpf_get_stack_offset
    touch /bpf_get_fs_offset/get_fs_offset
    touch /bpf_get_stack_offset/get_stack_offset
    exit 0
fi

LLVM_STRIP=llvm-strip
if ! command -v "$LLVM_STRIP" > /dev/null 2>&1 ; then
    LLVM_STRIP=llvm-strip-12
fi

LIBBPF_MAKE_FLAGS="BPFTOOL=/bpftool CLANG=clang-12 LLVM_STRIP=$LLVM_STRIP CFLAGS=-static"

cd / && git clone -b v0.0.2 --depth=1 --recurse-submodules https://github.com/Jongy/bpf_get_fs_offset.git
cd /bpf_get_fs_offset && git reset --hard 8326d39cf44845d4b643ed4267994afca8ccecb3
# shellcheck disable=SC2086
cd /bpf_get_fs_offset && make $LIBBPF_MAKE_FLAGS

cd / && git clone -b v0.0.3 --depth=1 --recurse-submodules https://github.com/Jongy/bpf_get_stack_offset.git
cd /bpf_get_stack_offset && git reset --hard 54b70ee65708cc8d3d7817277e82376d95205356
# shellcheck disable=SC2086
cd /bpf_get_stack_offset && make $LIBBPF_MAKE_FLAGS
