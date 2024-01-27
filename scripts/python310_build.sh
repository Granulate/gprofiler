#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

VERSION=3.10.13

wget "https://www.python.org/ftp/python/$VERSION/Python-$VERSION.tgz"
tar -xzf "Python-$VERSION.tgz"
cd "Python-$VERSION"
./configure --enable-optimizations --enable-shared --prefix=/usr LDFLAGS="-Wl,-rpath /usr/lib" --with-openssl=/usr --with-lto
make python install -j "$(nproc)"
