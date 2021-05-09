#!/bin/bash
set -e

isort --settings-file .isort.cfg .
flake8 --config .flake8 .
black --line-length 120 --skip-string-normalization --check .
mypy .
