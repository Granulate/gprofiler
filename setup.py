#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
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
