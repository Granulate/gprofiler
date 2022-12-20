#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -eu

# x86_64 container: Centos7 -> python3.7 built by code from node_builder_glibc_env.sh
# x86_64 exe: Centos7 -> python3.7 built by code from node_builder_glibc_env.sh
# aarch64 container: Centos8 -> python3.6.8 (default python from centos8 image) -> building with --static-libstdc++
# aarch64 exe: Centos8 -> python3.10 based from build-prepare stage where it is built -> building with --static-libstc++

yum update -y && yum install -y curl make git ca-certificates npm
# yum has node v10 by default, so we need to add newer version to run node-gyp
curl -fsSL https://rpm.nodesource.com/setup_16.x | bash -
yum remove -y nodejs npm
yum install -y nodejs
# node-gyp needs python 3.7+, so we need to install it
yum -y install openssl-devel bzip2-devel

if [ "$(uname -m)" != "aarch64" ]; then
    curl -L https://www.python.org/ftp/python/3.7.9/Python-3.7.9.tgz -o Python-3.7.9.tgz
    tar xzf Python-3.7.9.tgz
    cd Python-3.7.9
    ./configure --enable-optimizations
    make altinstall
    cd ..
    ln -sfn /usr/local/bin/python3.7 /usr/bin/python3
    ln -sfn /usr/local/bin/pip3.7 /usr/bin/pip3
else
    yum install -y libstdc++-static
fi
