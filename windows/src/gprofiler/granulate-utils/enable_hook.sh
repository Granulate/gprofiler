#!/bin/bash
set -e

ln -s "$(git rev-parse --show-toplevel)/lint.sh" "$(git rev-parse --git-path hooks)/pre-commit"
