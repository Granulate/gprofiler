#!/bin/bash
set -euo pipefail

#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
# Used in CI and checks that last pushed tag is greater than last existing tag.
# Using python package 'cmp_version' to do the compare work

pip install cmp_version
TAGS=$(git tag --sort=creatordate | tail -2)

# shellcheck disable=SC2206  # expansion is desired here to get array values
tags_array=(${TAGS//\n/ })

LATEST_TAG=${tags_array[0]}
NEW_TAG=${tags_array[1]}

if [ "$(cmp-version "$LATEST_TAG" "$NEW_TAG")" == "1" ]; then
    echo "New tag is older than the latest tag in git remote, not starting the build"
    exit 1
fi
