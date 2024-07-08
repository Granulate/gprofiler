#
# Copyright (C) 2022 Intel Corporation
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
import re
from pathlib import Path
from typing import Iterator

import setuptools


def read_requirements(path: str) -> Iterator[str]:
    with open(path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            # install_requires doesn't like paths
            if line.strip() == "./granulate-utils/":
                yield "granulate-utils"
                continue
            yield line


version = re.search(r'__version__\s*=\s*"(.*?)"', Path("gprofiler/__init__.py").read_text())
assert version is not None, "could not parse version!"

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="gprofiler",
    version=version.group(1),
    author="Granulate",
    author_email="",  # TODO
    description="Production Profiling, Made Easy",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Granulate/gprofiler",
    classifiers=[
        "Programming Language :: Python :: 3",
    ],
    packages=setuptools.find_packages(),
    include_package_data=True,
    install_requires=list(read_requirements("requirements.txt")),
    python_requires=">=3.8",
)
