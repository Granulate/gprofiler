#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
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