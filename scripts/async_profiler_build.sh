#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone --depth 1 -b v2.0g1 https://github.com/Granulate/async-profiler.git && cd async-profiler && git reset --hard 82c0da73fecd317f75c1241336aa036673d7e6c9
make release
