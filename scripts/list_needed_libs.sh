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

# this file lists all dynamic dependenices of executables in gprofiler/resources.
# we use it to let staticx know which libraries it should pack inside.
# (staticx knows to pack the libraries used by the executable we're packing. it doesn't know
# which executables are to be used by it)

EXCLUDED_DIRECTORIES=('"gprofiler/resources/node/*"')
FIND_BINS_CMD="find gprofiler/resources -executable -type f"

for DIR in "${EXCLUDED_DIRECTORIES[@]}" ; do
    FIND_BINS_CMD+=" -not -path $DIR"
done

BINS=$(eval "$FIND_BINS_CMD")

libs=

for f in $BINS ; do
    # no need to list binaries from libasyncProfiler.so - these do not run in our context,
    # so we don't care about their dependencies.
    if [[ "$f" == *"libasyncProfiler.so"* ]]; then
        continue
    fi

    set +e
    ldd_output="$(ldd "$f" 2>&1)"
    ret=$?
    set -e

    if [ $ret -eq 0 ]; then
        if [[ "$ldd_output" == *" not found"* ]]; then
            >&2 echo "missing libs/symbols for binary $f"
            >&2 echo "$ldd_output"
            exit 1
        fi

        libs="$libs $(grep -v vdso <<< "$ldd_output" | awk '$2 == "=>" { print $3 }')"
    elif [[ "$ldd_output" != *"not a dynamic executable"* ]]; then
        >&2 echo "ldd failed: $ldd_output"
        exit 1
    fi
done

printed=
for l in $libs ; do
    if ! echo "$printed" | grep -q "$l"; then
        >&2 echo "found needed lib: $l"
        echo -n " -l $l"
        printed="$printed $l"
    fi
done
