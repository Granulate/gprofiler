#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

curl -sSL https://codeload.github.com/Granulate/linux/zip/5ad1bebfc3ed2d1255f11986c627d77a15912710 -o linux.zip
unzip linux.zip
cd linux
make -C tools/perf LDFLAGS=-static
