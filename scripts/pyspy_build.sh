#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v0.3.7g1 https://github.com/Granulate/py-spy.git && git -C py-spy reset --hard 3837a0a76ccf2d3328100f9e47a25de3b5d9e9eb
cd py-spy
cargo build --release --target=x86_64-unknown-linux-musl
