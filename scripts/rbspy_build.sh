#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b rbspy-upgrade https://github.com/Granulate/rbspy.git
git -C rbspy reset --hard 09fae41d424137af6b150efee3947f0f684a8128
cd rbspy
cargo build --release --target="$(uname -m)"-unknown-linux-musl
