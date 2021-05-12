#!/usr/local/bin/python
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os


def lister():
    os.listdir("/")  # have some kernel stacks


if __name__ == "__main__":
    while True:
        lister()
