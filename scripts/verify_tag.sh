#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
set -euo pipefail

echo "RELEASE_VERSION=${GITHUB_REF#refs/*/}" >> "$GITHUB_ENV"
./scripts/cmp_tags.sh
echo "GH_REPO=${GITHUB_REPOSITORY,,}" >> "$GITHUB_ENV"
gprofiler_version=$(python -c "exec(open('gprofiler/__init__.py').read()); print(__version__)")
git_tag=$(git describe --tags)
if [ "$gprofiler_version" != "$git_tag" ]; then
    echo Running gprofiler_version "$gprofiler_version" but git_tag "$git_tag"
    exit 1
fi
