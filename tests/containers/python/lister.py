#!/usr/local/bin/python
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from threading import Thread


def lister():
    os.listdir("/")  # have some kernel stacks


def burner():
    while True:  # have some Python stacks
        pass


if __name__ == "__main__":
    Thread(target=burner).start()
    while True:
        lister()
