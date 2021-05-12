#!/usr/local/bin/python
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os


def fibonacci(n: int) -> int:
    return n if n <= 1 else fibonacci(n - 1) + fibonacci(n - 2)


if __name__ == "__main__":
    while True:
        os.listdir("/")  # have some kernel stacks
        fibonacci(15)
