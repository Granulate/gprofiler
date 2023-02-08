#!/usr/bin/env sh
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -exu

git clone --depth 1 -b "$(cat pyspy_tag.txt)" https://github.com/Granulate/py-spy.git && git -C py-spy reset --hard "$(cat pyspy_commit.txt)"
cd py-spy
cargo build --release --target="$(uname -m)"-unknown-linux-musl
