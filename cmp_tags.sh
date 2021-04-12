#!/bin/bash
set -ue

#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
# Used in CI and checks that last pushed tag is grater that last existing tag.
# Using python package 'cmp_version' to do the compare work

pip install cmp_version
git fetch origin
TAGS=$(git describe --tags $(git rev-list --tags --max-count=2)) # gets tags across all branches

tags_array=(${TAGS//\n/ })

NEW_TAG=${tags_array[0]}
LATEST_TAG=${tags_array[1]}


if [ "$(cmp-version "$LATEST_TAG" "$NEW_TAG")" == "1" ]; then
    echo "New tag is older than the latest tag in git remote, not starting the build"
    exit 1
fi
