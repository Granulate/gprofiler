#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

wget https://www.python.org/ftp/python/3.10.8/Python-3.10.8.tgz
tar -xzf Python-3.10.8.tgz
cd Python-3.10.8
./configure --enable-optimizations --enable-shared --prefix=/usr LDFLAGS="-Wl,-rpath /usr/lib" --with-openssl=/usr --with-lto
make python install -j "$(nproc)"
