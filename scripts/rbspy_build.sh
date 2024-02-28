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

git clone --depth 1 -b v0.12.1g1 https://github.com/Granulate/rbspy.git
git -C rbspy reset --hard dd4518391de1cf41e0615174541c17542efc96b9
cd rbspy
cargo build --release --target="$(uname -m)"-unknown-linux-musl
