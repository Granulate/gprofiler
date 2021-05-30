#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b granulate/on-cpu https://github.com/Granulate/rbspy.git && git -C rbspy reset --hard a2d305eaccb924df28d386b2331c56ba66d6fe5f
cd rbspy
cargo build --release --target=x86_64-unknown-linux-musl
