#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from typing import List

import setuptools


def read_requirements(path: str) -> List[str]:
    with open(path) as f:
        return [line for line in f.readlines() if not line.startswith("#")]


with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="gprofiler",
    version="1.0b1",
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
    install_requires=read_requirements("requirements.txt"),
    python_requires=">=3.6",
)
