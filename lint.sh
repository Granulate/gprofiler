#!/bin/bash
set -e

if [ -f venv/bin/activate ]; then
  source venv/bin/activate
fi

check_arg=""
if [[ "$1" = "--ci" ]]; then
    check_arg="--check"
fi

isort --settings-file .isort.cfg .
flake8 --config .flake8 .
black --line-length 120 --skip-string-normalization $check_arg .
mypy .
