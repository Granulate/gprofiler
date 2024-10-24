#!/usr/bin/env bash
#
# Copyright (C) 2022 Intel Corporation
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

# downloading the zip because the git is very large (this is also very large, but still smaller)
curl -SL https://codeload.github.com/Granulate/linux/zip/9909d736d8b8927d79003dfa9732050a08c11221 -o linux.zip
unzip -qq linux.zip
rm linux.zip
cd linux-*/

NO_LIBTRACEEVENT=1 NO_JEVENTS=1 make -C tools/perf LDFLAGS=-static -j "$(nproc)" perf
cp tools/perf/perf /
# need it static as well, even though it's used only during build (relies on libpcap, ...)
make -C tools/bpf LDFLAGS=-static -j "$(nproc)" bpftool
cp tools/bpf/bpftool/bpftool /
