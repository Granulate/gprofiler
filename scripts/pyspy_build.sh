#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v0.3.9g1 https://github.com/Granulate/py-spy.git && git -C py-spy reset --hard c05720104c3e7f93a4670497284282821c552bee
cd py-spy
cargo build --release --target=$(uname -m)-unknown-linux-musl
