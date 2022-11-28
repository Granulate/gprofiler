#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -eu

export DEBIAN_FRONTEND=noninteractive
apt update -y && apt install -y --no-install-recommends curl g++ python3 make gcc git ca-certificates npm
# apt has node v10 by default, so we need to add newer version to run node-gyp
curl -fsSL https://deb.nodesource.com/setup_16.x | bash -
apt install -y --no-install-recommends nodejs
