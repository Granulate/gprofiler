#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v0.3.7g3 https://github.com/Granulate/py-spy.git && git -C py-spy reset --hard 0aa5adace9c5d59cbf46b9c5b59ac92fb23b3e5a
cd py-spy
cargo build --release --target=$(uname -m)-unknown-linux-musl
