#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
"""Package name and version for Python files.

Some of the functions in this module are implemented based on similar functions
in pip 21.3.1, as mentioned in the functions' documentation.
"""
import csv
import email.parser
import functools
import os
import pathlib
import re
from typing import Dict, Iterator, Optional, Tuple

# pkg_resources is a part of setuptools, but it has a standalone deprecated version. That's the version mypy
# is looking for, but the stubs there are extremely deprecated
import pkg_resources
from granulate_utils.linux.ns import get_mnt_ns_ancestor, resolve_host_path
from psutil import AccessDenied, NoSuchProcess, Process

from gprofiler.log import get_logger_adapter

logger = get_logger_adapter(__name__)


__all__ = ["get_modules_versions"]


def _get_packages_dir(file_path: str) -> Optional[str]:
    if not file_path.startswith("/"):
        return None

    path, sep, _ = file_path.rpartition("/site-packages/")
    if sep == "":
        path, sep, _ = file_path.rpartition("/dist-packages/")

    if sep == "":
        return None

    return path + sep


def _get_metadata(dist: pkg_resources.Distribution) -> Dict[str, str]:
    """Based on pip._internal.utils.get_metadata"""
    metadata_name = "METADATA"
    if isinstance(dist, pkg_resources.DistInfoDistribution) and dist.has_metadata(metadata_name):  # type: ignore
        metadata = dist.get_metadata(metadata_name)
    elif dist.has_metadata("PKG-INFO"):
        metadata_name = "PKG-INFO"
        metadata = dist.get_metadata(metadata_name)
    else:
        metadata = None

    if metadata is None:
        return {}

    feed_parser = email.parser.FeedParser()
    feed_parser.feed(metadata)
    return dict(feed_parser.close())


def _convert_legacy_entry(entry: Tuple[str, ...], info: Tuple[str, ...]) -> str:
    """Based on pip._internal.commands.show._convert_legacy_entry.

    Convert a legacy installed-files.txt path into modern RECORD path.

    The legacy format stores paths relative to the info directory, while the
    modern format stores paths relative to the package root, e.g. the
    site-packages directory.

    :param entry: Path parts of the installed-files.txt entry.
    :param info: Path parts of the egg-info directory relative to package root.
    :returns: The converted entry.

    For best compatibility with symlinks, this does not use ``abspath()`` or
    ``Path.resolve()``, but tries to work with path parts:

    1. While ``entry`` starts with ``..``, remove the equal amounts of parts
       from ``info``; if ``info`` is empty, start appending ``..`` instead.
    2. Join the two directly.
    """
    while entry and entry[0] == "..":
        if not info or info[-1] == "..":
            info += ("..",)
        else:
            info = info[:-1]
        entry = entry[1:]
    return str(pathlib.Path(*info, *entry))


def _files_from_record(dist: pkg_resources.Distribution) -> Optional[Iterator[str]]:
    """Based on _files_from_record in pip._internal.commands.show.search_packages_info"""
    try:
        text = dist.get_metadata("RECORD")
    except (FileNotFoundError, KeyError):
        return None
    # This extra Path-str cast normalizes entries.
    return (str(pathlib.Path(row[0])) for row in csv.reader(text.splitlines()))


def _files_from_legacy(dist: pkg_resources.Distribution) -> Optional[Iterator[str]]:
    """Based on _files_from_legacy in pip._internal.commands.show.search_packages_info"""
    try:
        text = dist.get_metadata("installed-files.txt")
    except (FileNotFoundError, KeyError):
        return None
    paths = (p for p in text.splitlines(keepends=False) if p)
    root = dist.location
    info = dist.egg_info  # type: ignore
    if root is None or info is None:
        return paths
    try:
        info_rel = pathlib.Path(info).relative_to(root)
    except ValueError:  # info is not relative to root.
        return paths
    if not info_rel.parts:  # info *is* root.
        return paths
    return (_convert_legacy_entry(pathlib.Path(p).parts, info_rel.parts) for p in paths)


def _get_package_name(dist: pkg_resources.Distribution) -> Optional[str]:
    """Based on pip._internal.metadata.base.BaseDistribution.raw_name"""
    metadata = _get_metadata(dist)
    if metadata:
        # The metadata should NEVER be missing the Name: key, but if it somehow
        # does, fall back to the known canonical name.
        return metadata.get("Name", dist.project_name)
    return None


# Matches the path to libpython as it may appear in /proc/[pid]/maps, e.g. "/usr/local/lib/libpython3.9.so.1.0"
_LIBPYTHON_MAPS_PATTERN = re.compile(r"/\S*libpython\S*\.so(\.\S+)?\Z")


def _get_libpython_path(process: Process) -> Optional[str]:
    try:
        for mmap in process.memory_maps():
            match = _LIBPYTHON_MAPS_PATTERN.match(mmap.path)
            if match is not None:
                return match.group()
    except AccessDenied:
        logger.warning(f"Got AccessDenied when tried to read {process!r} mmaps")
    return None


# Matches PY_VERSION in Python's binary
# Notice that we'll match only 2.7 and 3.5-3.12
_PY_VERSION_STRING_PATTERN = re.compile(rb"(?<=\D)(?:2\.7|3\.(?:[5-9]|1[0-2]))\.\d\d?(?=\x00)")


@functools.lru_cache(maxsize=128)
def _get_python_full_version(process: Process) -> Optional[str]:
    bin_file = _get_libpython_path(process) or f"/proc/{process.pid}/exe"

    # Try to extract the version string from the binary
    try:
        f = open(resolve_host_path(process, bin_file), "rb")
    except OSError:
        return None

    with f:
        matches = _PY_VERSION_STRING_PATTERN.findall(f.read())
    if len(matches) != 1:
        # If we didn't match anything or for some reason matched more than once just don't get the version
        return None
    return str(matches[0].decode())  # Explicitly cast to str to silence mypy


# Standard library modules are identified by being under a pythonx.y dir and *not* under site/dist-packages
_STANDARD_LIB_PATTERN = re.compile(r"/python\d\.\d\d?/(?!.*(site|dist)-packages)")


def _populate_standard_libs_version(result: Dict[str, Optional[Tuple[str, str]]], process: Process) -> None:
    py_version = None

    for path in result:
        match = _STANDARD_LIB_PATTERN.search(path)
        if match is None:
            # This module is (probably) not part of the standard library
            continue

        if py_version is None:
            py_version = _get_python_full_version(process)
            if py_version is None:
                # No need to continue trying if we failed
                return

        result[path] = ("standard-library", py_version)


@functools.lru_cache(maxsize=128)
def _get_packages_files(process: Process, packages_path: str) -> Dict[str, Tuple[str, str]]:
    """Return a dict of filename: (package_name, package_version) for the packages in packages_path"""
    # Transform packages_path to be relative to /proc/[pid]/root/
    packages_host_path = resolve_host_path(process, packages_path)

    path_to_package_info = {}
    for dist in pkg_resources.find_distributions(packages_host_path):
        files_iter = _files_from_record(dist) or _files_from_legacy(dist)
        if files_iter is not None:
            pacakge_name = _get_package_name(dist)
            if pacakge_name is not None:
                path_to_package_info.update(
                    dict.fromkeys(
                        (os.path.join(packages_path, file) for file in files_iter), (pacakge_name, dist.version)
                    )
                )
    return path_to_package_info


_warned_no__normalized_cached = False


def _populate_packages_versions(packages_versions: Dict[str, Optional[Tuple[str, str]]], process: Process) -> None:
    # A little monkey patch to prevent pkg_resources from converting "/proc/{pid}/root/" to "/".
    # This function resolves symlinks and makes paths absolute for comparison purposes which isn't required
    # for our usage.
    if hasattr(pkg_resources, "_normalize_cached"):
        original__normalize_cache = pkg_resources._normalize_cached  # type: ignore
        pkg_resources._normalize_cached = lambda path: path  # type: ignore
    else:
        global _warned_no__normalized_cached
        if not _warned_no__normalized_cached:
            # Log only once so we don't spam the log
            logger.warning("Cannot get modules version, pkg_resources has no '_normalize_cached' attribute")
            _warned_no__normalized_cached = True

        # Not much that we can do, pkg_resources.find_distributions won't work properly
        return

    # We use the process only for its /proc/[pid]/root, so it's more efficient for caching purposes to use the
    # ns ancestor
    ancestor = get_mnt_ns_ancestor(process)

    try:
        for module_path in packages_versions:
            if not module_path.startswith("/"):
                continue

            packages_path = _get_packages_dir(module_path)
            if packages_path is None:
                # This module is (probably) not part of a package
                continue
            path_to_package_info = _get_packages_files(ancestor, packages_path)
            package_info: Optional[Tuple[str, str]] = path_to_package_info.get(module_path)
            if package_info is not None:
                packages_versions[module_path] = package_info
    finally:
        # Don't forget to restore the original implementation in case someone else uses this function
        pkg_resources._normalize_cached = original__normalize_cache  # type: ignore


_exceptions_logged = 0


def get_modules_versions(modules_paths: Iterator[str], process: Process) -> Dict[str, Optional[Tuple[str, str]]]:
    """Return a dict with module_path: (package_name, version).

    If the module is from Python's standard library, package_name is
    "standard-library" and the version is Python's version.
    If couldn't determine the version the value is None.

    modules_paths must be absolute. pid is required to access the path via
    /proc/{pid}/root/.

    Given a path to a module, it's not trivial to determine its package as some
    packages contain modules with completely unrelated names (e.g. the module
    pkg_resources used in this function is a part of the setuptools package).
    This function gathers info for all the packages in the (dist|site)-packages
    directory in each path, including a list of all the files associated with
    each package. This list is searched for the given module path.
    """
    result = dict.fromkeys(modules_paths)
    try:
        _populate_standard_libs_version(result, process)
        _populate_packages_versions(result, process)
    except NoSuchProcess:
        # The process died during the function. That's just the way of life, it's expected
        pass
    except Exception:
        # Make sure to catch any exception. If something goes wrong just don't get the version, we shouldn't
        # interfere with gProfiler
        global _exceptions_logged
        if _exceptions_logged < 10:
            logger.exception(f"Failed to get modules versions for {process!r}:")
            # Don't spam the log
            _exceptions_logged += 1
    return result
