#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

# downloading the zip because the git is very large (this is also very large, but still smaller)
curl -SL https://codeload.github.com/Granulate/linux/zip/5c103bf97fb268e4ea157f5e1c2a5bd6ad8c40dc -o linux.zip
unzip -qq linux.zip
rm linux.zip
cd linux-*/

NO_LIBTRACEEVENT=1 NO_JEVENTS=1 make -C tools/perf LDFLAGS=-static -j "$(nproc)" perf
cp tools/perf/perf /
# need it static as well, even though it's used only during build (relies on libpcap, ...)
make -C tools/bpf LDFLAGS=-static -j "$(nproc)" bpftool
cp tools/bpf/bpftool/bpftool /
