#!/usr/bin/env bash
#
# Copyright (C) 2023 Intel Corporation
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#    http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
