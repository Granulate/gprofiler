#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

mkdir -p /tmp/dotnet/deps
while read -r i  ; do
   cp "/usr/share/dotnet/shared/Microsoft.NETCore.App/6.0.7/$i" "/tmp/dotnet/deps/$i"
done <./dotnet_trace_dependencies.txt