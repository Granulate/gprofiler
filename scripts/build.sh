#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -e

mkdir -p gprofiler/resources

function curl_with_timecond() {
    url="$1"
    output="$2"
    if [ -f "$output" ]; then
        time_cond="-z $output"
    else
        time_cond=""
    fi
    curl -fL "$url" $time_cond -o "$output"
}

# burn
curl_with_timecond https://github.com/Granulate/burn/releases/download/v1.0.1g2/burn gprofiler/resources/burn
chmod +x gprofiler/resources/burn
