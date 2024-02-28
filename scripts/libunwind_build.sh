#!/usr/bin/env sh
#
# Copyright (C) 2023 Intel Corporation
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
set -eu

LIBUNWIND_VERSION=1.6.2
LIBUNWIND_TAR=libunwind-"$LIBUNWIND_VERSION".tar.gz

curl -L https://github.com/libunwind/libunwind/releases/download/v"$LIBUNWIND_VERSION"/"$LIBUNWIND_TAR" -o "$LIBUNWIND_TAR"
tar -xf "$LIBUNWIND_TAR"
cd libunwind-"$LIBUNWIND_VERSION"

if [ "$(uname -m)" = "aarch64" ]; then
    # higher value for make -j kills the GH runner (build gets OOM)
    nproc=2
else
    nproc=$(nproc)
fi

prefix_flag=""
if [ "$#" -gt 2 ]; then
    echo "Too many arguments"
elif [ "$#" -eq 1 ]; then
    prefix_flag="--prefix=$1"
fi

./configure --disable-tests --disable-documentation "$prefix_flag" && make install -j "$nproc"
cd ..
rm -r libunwind-"$LIBUNWIND_VERSION"
rm "$LIBUNWIND_TAR"
