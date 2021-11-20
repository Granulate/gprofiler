#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

LIBBPF_MAKE_FLAGS="BPFTOOL=/bpftool CLANG=clang-10 LLVM_STRIP=llvm-strip-10 CFLAGS=-static"

git clone -b v0.0.1 --depth=1 --recurse-submodules https://github.com/Jongy/bpf_get_fs_offset.git && cd bpf_get_fs_offset && git reset --hard 85bbdb3d3b54406944a0f6d8c77117e4d4a35f3e
cd /bpf_get_fs_offset && make $LIBBPF_MAKE_FLAGS

git clone -b v0.0.1 --depth=1 --recurse-submodules https://github.com/Jongy/bpf_get_stack_offset.git && cd bpf_get_fs_offset && git reset --hard 7e1aa6148efe2abea54fb5ffb332da2e6426396c
cd /bpf_get_stack_offset && make $LIBBPF_MAKE_FLAGS
