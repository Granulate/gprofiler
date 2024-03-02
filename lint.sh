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
