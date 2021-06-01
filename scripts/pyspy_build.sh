#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v0.3.7g2 https://github.com/Granulate/py-spy.git && git -C py-spy reset --hard 34a2a2bf7c324e08f31e83b3b34cff36d662c988
cd py-spy
cargo build --release --target=x86_64-unknown-linux-musl
