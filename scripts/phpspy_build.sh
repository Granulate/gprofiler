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

# First build phpspy
pushd /tmp
git clone --depth=1 -b v0.6.0-g4 --recursive https://github.com/Granulate/phpspy.git && git -C phpspy reset --hard af1d74ebd4f9c27486e82397eafde1c450f06510
cd /tmp/phpspy
make
popd

# Then, build binutils - required for static objdump

BINUTILS_VERSION=2.25

mkdir -p /tmp/binutils
pushd /tmp/binutils
wget http://ftp.gnu.org/gnu/binutils/binutils-$BINUTILS_VERSION.tar.gz
tar xzf binutils-$BINUTILS_VERSION.tar.gz
cd binutils-$BINUTILS_VERSION
./configure --disable-nls --prefix="$(pwd)"/bin --disable-ld --disable-gdb
make configure-host
make LDFLAGS="-all-static"
make install
popd
