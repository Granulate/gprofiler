#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

LLVM_STRIP=llvm-strip
if ! command -v "$LLVM_STRIP" > /dev/null 2>&1 ; then
    LLVM_STRIP=llvm-strip-10
fi

LIBBPF_MAKE_FLAGS="BPFTOOL=/bpftool CLANG=clang-10 LLVM_STRIP=$LLVM_STRIP CFLAGS=-static"

cd / && git clone -b aarch64 --depth=1 --recurse-submodules https://github.com/Jongy/bpf_get_fs_offset.git
cd /bpf_get_fs_offset && git reset --hard 094e93f979308d46dffb8d4ea88823f68d53ba85
cd /bpf_get_fs_offset && make $LIBBPF_MAKE_FLAGS

cd / && git clone -b aarch64 --depth=1 --recurse-submodules https://github.com/Jongy/bpf_get_stack_offset.git
cd /bpf_get_stack_offset && git reset --hard d8b77ce6da674c38ad0bb856686fde1e63ad0814
cd /bpf_get_stack_offset && make $LIBBPF_MAKE_FLAGS
