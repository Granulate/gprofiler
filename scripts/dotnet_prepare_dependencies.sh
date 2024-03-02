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

mkdir -p /tmp/dotnet/deps
declare -a linux_deps=("libclrjit.so"
                       "libhostpolicy.so"
                       "libcoreclr.so"
                       "libSystem.Native.so"
                       "libSystem.Security.Cryptography.Native.OpenSsl.so"
                       )
for i in "${linux_deps[@]}"
do
   cp "/usr/share/dotnet/shared/Microsoft.NETCore.App/6.0.7/$i" "/tmp/dotnet/deps/$i"
done
while read -r i  ; do
   cp "/usr/share/dotnet/shared/Microsoft.NETCore.App/6.0.7/$i" "/tmp/dotnet/deps/$i"
done <./dotnet_trace_dependencies.txt