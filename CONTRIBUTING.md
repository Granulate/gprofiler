# Contributing Guide

Contributing to `gprofiler` is easy. This document shows you how to
get the project, run all provided tests and generate a production-ready build.

## Dependencies

To make sure that the following instructions work, please install the following dependencies
on you machine:

- Git
- Python 3.6+

## Installation

To get the source of `gprofiler`, clone the git repository via:
````bash
git clone --recursive https://github.com/granulate/gprofiler
````

Make sure to clone in `--recursive` mode! The project uses submodules. If you didn't clone recursively, run `git submodule update --init`.

This will clone the complete source to your local machine. Navigate to the project folder
and install all needed dependencies with:
````bash
pip3 install -r requirements.txt
pip3 install -r dev-requirements.txt
````

This above commands installs all packages required for linting and testing. For a local build, no dependencies are needed (see [building](#building) ahead)

## Building

There are build scripts under `scripts/` for all components gProfiler uses.
They are all invoked during the gProfiler Docker image build & gProfiler executable build (described ahead). Do not invoke them manually as each requires a different OS & installed tools to run - they are invoked properly during the full build.

The full build builds from source all profilers used by gProfiler. It can take 20-30 minutes on an 8-cores machine and requires 16 GB of RAM. It might work with less but the build containers might get OOMs.

### Docker image build
You can build the docker image, including all bundled dependencies, through:
```bash
# x86_64
./scripts/build_x86_64_container.sh -t gprofiler
# aarch64
./scripts/build_aarch64_container.sh -t gprofiler
```
These will create a local image named `gprofiler`.

### Executable build
You can build executable, including all bundled dependencies, through:
```bash
# x86_64
./scripts/build_x86_64_executable.sh
# aarch64
./scripts/build_aarch64_executable.sh
```
These will create an executable in `build/{arch}/gprofiler`.

### Cross-building
Both the container & executable build scripts can run over Docker multiarch support. It will be much slower though.
If you don't have Docker multiarch configured on your host, you can do that via:
```bash
docker run --rm --privileged multiarch/qemu-user-static --reset -p yes
docker buildx create --name multiarch --driver docker-container --use --node multiarch0
```
Following that, you can run the build script for a cross architecture.

### Accessing build artifacts

If you want particular artifacts (e.g one of the built profilers) but don't want to build the entire profiler, you can copy the built artifacts from the latest Docker image build; there's a helper script to do that:
```bash
./scripts/copy_resources_from_image.sh
```

The above command will get the `granulate/gprofiler:latest` image and extract all dependencies to the `gprofiler/resources` directory.

### Debugging the build

If a certain build step fails, it helps to have access to the failing build layer when debugging it.

This can be achieved in 2 steps:
1. Adding a `FROM A AS B` line right below the failing line, where `A` is the name given to the previous base layer. For example, if these are the layers in question:  
```
FROM ubuntu:20.04 AS mylayer
ADD ...
RUN ...
RUN .....  # THIS ONE FAILS!
```
then we'd add this layer:
```
FROM ubuntu:20.04 AS mylayer
ADD ...
RUN ...
FROM mylayer AS mylayer2
RUN .....  # THIS ONE FAILS!
```
2. In the relevant `build_{arch}_{target}.sh` script, remove `--output` if it's the executable one, and add `--target mylayer`. This will cause Docker to run the build of the requested layer (and its dependencies) *until* `mylayer2` begins, and the result is a layer which we can use with `docker run` and try out the failing `RUN` command on.

## Linting

Make sure you have installed `requirements.txt` and `dev-requirements.txt` as described in the [installation](#installation) section, and make sure the versions match as well (`black` of different versions, for example, may yield different formatting results).

The Python linters & formatters can be run with `./lint.sh`. The Dockerfile linter can be run with `./dockerfile_lint.sh`. The shell linter can be run with `./shell_lint.sh`.

## Testing
Tests require to run as root, so make sure that the Python environment as described in [installation](#installation) is installed properly for "root" as well.

To run all automated tests simply run:
```bash
sudo ./tests/test.sh
```

To run specific tests you can use:
```bash
cd tests && sudo python3 -m pytest -v -k "test_..."
```

## Contributing to gProfiler

### Reporting issues
If you have identified an issue or a bug, or have a feature request we want to hear about it! Here's how to make reporting effective as possible:

#### Look For an Existing Issue

Before you create a new issue, please do a search in open issues to see if the issue or feature request has already been filed.

Be sure to scan through the most popular feature requests.

If you find your issue already exists, make relevant comments and add your reaction. Use a reaction in place of a "+1" comment:

* 👍 - upvote
* 👎 - downvote

If you cannot find an existing issue that describes your bug or feature, create a new issue using the guidelines below.

#### Writing Good Bug Reports and Feature Requests
Please open a single issue per problem and/or feature request, and try to provide as much information as you can. This will help reproduce the issue or implement the new functionality.

When opening an issue you should follow the specific issue template for directions and try to provide all the information necessary.
