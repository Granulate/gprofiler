#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b granulate-master https://github.com/Granulate/rbspy.git && git -C rbspy reset --hard b69a3131b8c8581b1c249e7394524845eaa5eca1
cd rbspy
cargo build --release --target=x86_64-unknown-linux-musl
