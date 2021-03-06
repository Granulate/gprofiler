#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

mkdir -p build

# async-profiler
mkdir -p gprofiler/resources/java
curl -fL https://github.com/Granulate/async-profiler/releases/download/v2.0g1/async-profiler-2.0-linux-x64.tar.gz \
   -z build/async-profiler-2.0-linux-x64.tar.gz -o build/async-profiler-2.0-linux-x64.tar.gz
tar -xzf build/async-profiler-2.0-linux-x64.tar.gz -C gprofiler/resources/java --strip-components=2 async-profiler-2.0-linux-x64/build

# py-spy
mkdir -p gprofiler/resources/python
curl -fL https://github.com/Granulate/py-spy/releases/download/v0.3.5g1/py-spy -o gprofiler/resources/python/py-spy
chmod +x gprofiler/resources/python/py-spy

# pyperf - just create the directory for it, it will be built/downloaded later
mkdir -p gprofiler/resources/python/pyperf

# perf
curl -fL https://github.com/Granulate/linux/releases/download/v5.12g1/perf -z gprofiler/resources/perf -o gprofiler/resources/perf
chmod +x gprofiler/resources/perf

# burn
curl -fL https://github.com/Granulate/burn/releases/download/v1.0.1g2/burn -z gprofiler/resources/burn -o gprofiler/resources/burn
chmod +x gprofiler/resources/burn

rm -r build
