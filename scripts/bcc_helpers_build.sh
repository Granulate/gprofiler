#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# TODO support aarch64, after we support it in PyPerf
if [ $(uname -m) != "x86_64" ]; then
    mkdir -p /bpf_get_fs_offset /bpf_get_stack_offset
    touch /bpf_get_fs_offset/get_fs_offset
    touch /bpf_get_stack_offset/get_stack_offset
    exit 0
fi

LLVM_STRIP=llvm-strip
if ! command -v "$LLVM_STRIP" > /dev/null 2>&1 ; then
    LLVM_STRIP=llvm-strip-10
fi

LIBBPF_MAKE_FLAGS="BPFTOOL=/bpftool CLANG=clang-10 LLVM_STRIP=$LLVM_STRIP CFLAGS=-static"

cd / && git clone -b v0.0.1 --depth=1 --recurse-submodules https://github.com/Jongy/bpf_get_fs_offset.git
cd /bpf_get_fs_offset && git reset --hard 85bbdb3d3b54406944a0f6d8c77117e4d4a35f3e
cd /bpf_get_fs_offset && make $LIBBPF_MAKE_FLAGS

cd / && git clone -b v0.0.1 --depth=1 --recurse-submodules https://github.com/Jongy/bpf_get_stack_offset.git
cd /bpf_get_stack_offset && git reset --hard 7e1aa6148efe2abea54fb5ffb332da2e6426396c
cd /bpf_get_stack_offset && make $LIBBPF_MAKE_FLAGS
