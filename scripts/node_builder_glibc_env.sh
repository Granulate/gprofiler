#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -eu

# x86_64 container: Centos7 -> python3.6.8 (default python3 installed by yum in centos7 image)
# x86_64 exe: Centos7 -> python3.6.8 (default python3 installed by yum in centos7 image)
# aarch64 container: Centos8 -> python3.6.8 (default python3 installed by yum in centos8 image) -> building with --static-libstdc++
# aarch64 exe: Centos8 -> python3.10 based from build-prepare stage where it is built -> building with --static-libstc++

yum update -y && yum install -y curl make git ca-certificates
if [ ! -f "/usr/bin/python3" ]; then
    yum install -y python3
fi
# yum has node v10 by default, so we need to add newer version to run node-gyp
curl -fsSL https://rpm.nodesource.com/setup_16.x | bash -
yum install -y nodejs
yum install -y openssl-devel bzip2-devel

if [ "$(uname -m)" = "aarch64" ]; then
    yum install -y libstdc++-static
fi
