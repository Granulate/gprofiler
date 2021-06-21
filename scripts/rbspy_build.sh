#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v0.7.0g1 https://github.com/Granulate/rbspy.git && git -C rbspy reset --hard 5f27dc892e70973bc1d6430b1c208ec152448e18
cd rbspy
cargo build --release --target=x86_64-unknown-linux-musl
