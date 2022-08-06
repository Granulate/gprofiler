#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

apt-get update
apt-get install -y --no-install-recommends \
  git \
  ca-certificates \
  wget \
  make \
  libc6-dev \
  gcc
