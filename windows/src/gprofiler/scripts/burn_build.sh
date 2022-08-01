#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

git clone -b v1.0.1g2 https://github.com/granulate/burn
cd burn
git reset --hard 40d34547e942c53b6b1d1dd660eaf6367d2b8489

CGO_ENABLED=0 go build
