#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail
cd /usr/src
wget https://www.openssl.org/source/openssl-1.1.1q.tar.gz
tar -xzvf openssl-1.1.1q.tar.gz
cd openssl-1.1.1q
./config --prefix=/usr --openssldir=/etc/ssl --libdir=lib no-shared zlib-dynamic
make
make install
