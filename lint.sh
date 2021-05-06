#!/bin/bash
set -e
if [ -f venv/bin/activate ]; then
  source venv/bin/activate
fi

flake8 --config .flake8 .
isort --settings-file .isort.cfg .
black --line-length 120 --skip-string-normalization --check .
mypy .
