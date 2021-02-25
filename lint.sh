#!/bin/bash

flake8 . --max-line-length=120
black . --line-length 120 --check
mypy .
