#!/usr/local/bin/python
#
# Copyright (C) 2023 Intel Corporation
# 
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
# 
#    http://www.apache.org/licenses/LICENSE-2.0
# 
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
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
