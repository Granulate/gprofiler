#!/usr/bin/env sh
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -eu

git clone --depth 1 -b v0.12.1g1 https://github.com/Granulate/rbspy.git
git -C rbspy reset --hard dd4518391de1cf41e0615174541c17542efc96b9
cd rbspy
cargo build --release --target="$(uname -m)"-unknown-linux-musl
