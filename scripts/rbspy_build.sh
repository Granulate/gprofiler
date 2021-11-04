#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v0.8.1g1 https://github.com/Granulate/rbspy.git
git -C rbspy reset --hard a592b03f6d447f9c4fb1df49f4f60531d8395c5f
cd rbspy
cargo build --release --target=$(uname -m)-unknown-linux-musl
