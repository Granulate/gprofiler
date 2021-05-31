#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

yum install -y gcc g++ gcc-c++.x86_64 make java-1.8.0-openjdk-devel glibc-static git
