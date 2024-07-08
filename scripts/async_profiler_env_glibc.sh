#!/usr/bin/env bash
#
# Copyright (C) 2022 Intel Corporation
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
set -euo pipefail

yum install -y make java-11-openjdk-devel glibc-static git gcc gcc-c++ libstdc++-static

# on x86_64 - we build against glibc 2.12 which is provided by compat-glibc.
if [ "$(uname -m)" = "x86_64" ]; then
    yum install -y compat-glibc
fi
