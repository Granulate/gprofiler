#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
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
