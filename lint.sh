#!/bin/bash
set -e

if [ -f venv/bin/activate ]; then
  source venv/bin/activate
fi

check_arg=""
if [[ "$1" = "--ci" ]]; then
    check_arg="--check"
fi

isort --settings-path .isort.cfg --skip granulate-utils .
black --line-length 120 $check_arg --exclude "granulate-utils|\.venv" .
flake8 --config .flake8 .
mypy .
