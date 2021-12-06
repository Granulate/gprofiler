#!/usr/local/bin/python
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from threading import Thread

import pkg_resources  # type: ignore


def lister():
    os.listdir("/")  # have some kernel stacks


def burner():
    while True:  # have some Python stacks
        pass


def getter():
    while True:
        # Have some package stacks.
        # Notice that we're using a module from a package with a different name - setuptools
        pkg_resources.get_platform()


if __name__ == "__main__":
    Thread(target=burner).start()
    Thread(target=getter).start()
    while True:
        lister()
