#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -eu

yum update -y && yum install -y curl python3 make git ca-certificates npm
# yum has node v10 by default, so we need to add newer version to run node-gyp
curl -fsSL https://rpm.nodesource.com/setup_16.x | bash -
yum install -y nodejs
# node-gyp needs python 3.7+, so we need to install it
yum -y install openssl-devel bzip2-devel
curl -L https://www.python.org/ftp/python/3.7.9/Python-3.7.9.tgz -o Python-3.7.9.tgz
tar xzf Python-3.7.9.tgz
cd Python-3.7.9
./configure --enable-optimizations
make altinstall
cd ..
