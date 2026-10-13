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

set -ueo pipefail

if [ -z "${GPROFILER_TOKEN}" ]; then echo "missing GPROFILER_TOKEN!"; exit 1; fi
if [ -z "${GPROFILER_SERVICE}" ]; then echo "missing GPROFILER_SERVICE!"; exit 1; fi

# Map the user-facing GPROFILER_SERVICE to gprofiler's configargparse env var name.
# configargparse (auto_env_var_prefix="gprofiler_") derives env var names from the long
# option: --token -> GPROFILER_TOKEN (already matches), --service-name -> GPROFILER_SERVICE_NAME.
GPROFILER_SERVICE_NAME="${GPROFILER_SERVICE}"

HERE=$(dirname -- "$0")
UNIT_NAME=granulate-gprofiler.service
TEMPLATE=$HERE/$UNIT_NAME.template

if [ ! -f "$TEMPLATE" ]; then
    echo "Downloading template"
    wget https://raw.githubusercontent.com/intel/gprofiler/master/deploy/systemd/granulate-gprofiler.service.template -O "$TEMPLATE"
fi

ENV_FILE_NAME=granulate-gprofiler.env

for f in "$UNIT_NAME" "$ENV_FILE_NAME"; do
    if [ -e "$f" ]; then echo "${f} already exists, please remove it and re-run (and disable the service if installed from symlink)"; exit 1; fi
done

# Values are single-quoted in the environment file, so they can't contain single quotes or newlines.
for v in "$GPROFILER_TOKEN" "$GPROFILER_SERVICE_NAME"; do
    case "$v" in *"'"* | *$'\n'*) echo "GPROFILER_TOKEN and GPROFILER_SERVICE must not contain single quotes or newlines"; exit 1 ;; esac
done

# Create the file as 0600 from the start (umask in a subshell) so the token is never readable by other users.
(
    umask 077
    printf "GPROFILER_TOKEN='%s'\nGPROFILER_SERVICE_NAME='%s'\n" "$GPROFILER_TOKEN" "$GPROFILER_SERVICE_NAME" > "$ENV_FILE_NAME"
)
FULL_ENV_FILE_PATH=$(realpath -s "$ENV_FILE_NAME")

# Escape characters that are special in the sed replacement (using "|" as the delimiter).
ESCAPED_ENV_FILE_PATH=$(printf '%s' "$FULL_ENV_FILE_PATH" | sed 's/[&|\\]/\\&/g')
sed "s|@ENV_FILE@|${ESCAPED_ENV_FILE_PATH}|g" < "$TEMPLATE" > "$UNIT_NAME"

FULL_SERVICE_FILE_PATH=$(realpath -s "$UNIT_NAME")

echo "created ${FULL_SERVICE_FILE_PATH} and ${FULL_ENV_FILE_PATH} (mode 0600, holds the credentials)!"
echo -e "you can now install and start the service by running:\nsystemctl enable ${FULL_SERVICE_FILE_PATH}\nsystemctl start ${UNIT_NAME}"
