#!/usr/bin/env bash
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
set -e

if [ -f venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source venv/bin/activate
fi

black_extra_args=""
isort_extra_args=""
if [[ "$1" = "--ci" ]]; then
    black_extra_args="--check"
    isort_extra_args="--check-only"
fi

isort --settings-path .isort.cfg $isort_extra_args .
black --line-length 120 $black_extra_args --exclude "granulate-utils|.*venv.*" .
flake8 --config .flake8 .
mypy .
