#!/usr/bin/env sh
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v0.3.12g1 https://github.com/Granulate/py-spy.git && git -C py-spy reset --hard b45bd56ee865835ed2738ed5c994a94590d30703
cd py-spy
cargo build --release --target="$(uname -m)"-unknown-linux-musl
