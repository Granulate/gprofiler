#!/bin/bash
set -e

ln -s "../../lint.sh" "$(git rev-parse --show-toplevel)/.git/hooks/pre-commit"
