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

VERSION=3.10.13

wget "https://www.python.org/ftp/python/$VERSION/Python-$VERSION.tgz"
tar -xzf "Python-$VERSION.tgz"
cd "Python-$VERSION"
./configure --enable-optimizations --enable-shared --prefix=/usr LDFLAGS="-Wl,-rpath /usr/lib" --with-openssl=/usr --with-lto
make python install -j "$(nproc)"
