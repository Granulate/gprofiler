#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v0.8.1g2 https://github.com/Granulate/rbspy.git
git -C rbspy reset --hard 2ce395058264d2d832d9a89589d52f26ccd55636
cd rbspy
cargo build --release --target="$(uname -m)"-unknown-linux-musl
