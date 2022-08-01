#!/bin/bash
set -e

if [ -f venv/bin/activate ]; then
  source venv/bin/activate
fi

check_arg=""
if [[ "$1" = "--ci" ]]; then
    check_arg="--check"
fi

# see also isort --skip and flake8 config.
EXCLUDE_RE='venv/bin/|build|granulate_utils/generated'

isort --settings-path .isort.cfg --skip granulate_utils/generated .
black --line-length 120 $check_arg --exclude $EXCLUDE_RE .
flake8 --config .flake8  .
mypy --exclude $EXCLUDE_RE .
