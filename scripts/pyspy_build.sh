#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v0.3.10g1 https://github.com/Granulate/py-spy.git && git -C py-spy reset --hard 480deec8b5dde3cd331d1a793106981c1796d172
cd py-spy
cargo build --release --target="$(uname -m)"-unknown-linux-musl
