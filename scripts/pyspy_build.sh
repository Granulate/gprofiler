#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v0.3.6g1 https://github.com/Granulate/py-spy.git && git -C py-spy reset --hard fcf4aa16587ae0b425d7533b828827901d14b24e
cd py-spy
cargo build --release --target=x86_64-unknown-linux-musl
