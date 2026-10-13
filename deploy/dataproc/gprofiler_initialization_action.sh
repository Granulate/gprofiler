#!/bin/bash
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
set -euxo pipefail

# Pass credentials via env vars instead of command-line args to avoid
# exposing them in /proc/<pid>/cmdline (world-readable on Linux).
GPROFILER_TOKEN=$(/usr/share/google/get_metadata_value attributes/gprofiler-token)
readonly GPROFILER_TOKEN
export GPROFILER_TOKEN

GPROFILER_SERVICE_NAME=$(/usr/share/google/get_metadata_value attributes/gprofiler-service)
readonly GPROFILER_SERVICE_NAME
export GPROFILER_SERVICE_NAME

ENABLE_STDOUT=$(/usr/share/google/get_metadata_value attributes/enable-stdout)
readonly ENABLE_STDOUT

SPARK_METRICS=$(/usr/share/google/get_metadata_value attributes/spark-metrics || true)
readonly SPARK_METRICS

OUTPUT_REDIRECTION=""
if [ "$ENABLE_STDOUT" != "1" ]; then
  OUTPUT_REDIRECTION="> /dev/null 2>&1"
fi

flags=""
if [[ "$SPARK_METRICS" == "1" ]]; then
	flags="$flags --collect-spark-metrics"
fi

wget --no-verbose "https://github.com/intel/gprofiler/releases/latest/download/gprofiler_$(uname -m)" -O gprofiler
sudo chmod +x gprofiler
# Token and service name are passed via environment variables (exported above)
# to avoid exposing credentials in /proc/<pid>/cmdline
sudo --preserve-env=GPROFILER_TOKEN,GPROFILER_SERVICE_NAME \
    sh -c "setsid ./gprofiler -cu $flags $OUTPUT_REDIRECTION &"
echo "gProfiler installed successfully."
