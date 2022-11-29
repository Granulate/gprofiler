#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

yum install -y make java-11-openjdk-devel glibc-static git gcc gcc-c++

if [ "$(uname -m)" = "x86_64" ]; then
    yum install -y compat-glibc
fi
