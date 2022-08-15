#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import re
from pathlib import Path
from typing import List

import setuptools


def read_requirements(path: str) -> List[str]:
    with open(path) as f:
        return [line for line in f.readlines() if not line.startswith("#")]


version = re.search(r'__version__\s*=\s*"(.*?)"', Path("granulate_utils/__init__.py").read_text())
assert version is not None, "could not parse version!"

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

setuptools.setup(
    name="granulate_utils",
    version=version.group(1),
    author="Granulate",
    author_email="",  # TODO
    description="Granulate Python utilities",
    long_description=long_description,
    long_description_content_type="text/markdown",
    url="https://github.com/Granulate/granulate-utils",
    classifiers=[
        "Programming Language :: Python :: 3",
    ],
    packages=setuptools.find_packages(),
    package_data={"granulate_utils": ["py.typed"]},
    include_package_data=True,
    install_requires=read_requirements("requirements.txt"),
    python_requires=">=3.6",
)
