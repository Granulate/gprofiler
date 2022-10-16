#!/usr/local/bin/python
#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
from threading import Thread


class Lister(object):
    @classmethod
    def lister(cls):
        # type: () -> None
        os.listdir("/")  # have some kernel stacks & Python stacks from a class method


class Burner(object):
    def burner(self):
        # type: () -> None
        while True:  # have some Python stacks from an instance method
            pass


def parser():
    # type: () -> None
    try:
        import yaml
    except ImportError:
        return  # not required in this test
    while True:
        # Have some package stacks.
        # Notice the name of the package name (PyYAML) is different from the name of the module (yaml)
        yaml.parse("")  # type: ignore


if __name__ == "__main__":
    Thread(target=Burner().burner).start()
    Thread(target=parser).start()
    lister = Lister()
    while True:
        lister.lister()
