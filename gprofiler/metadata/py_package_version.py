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
import pkg_resources  # type: ignore
from granulate_utils.linux.ns import resolve_host_path

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


def _get_metadata(dist: pkg_resources.Distribution) -> dict:
    """Based on pip._internal.utils.get_metadata"""
    metadata_name = "METADATA"
    if isinstance(dist, pkg_resources.DistInfoDistribution) and dist.has_metadata(metadata_name):
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
    except FileNotFoundError:
        return None
    # This extra Path-str cast normalizes entries.
    return (str(pathlib.Path(row[0])) for row in csv.reader(text.splitlines()))


def _files_from_legacy(dist: pkg_resources.Distribution) -> Optional[Iterator[str]]:
    """Based on _files_from_legacy in pip._internal.commands.show.search_packages_info"""
    try:
        text = dist.get_metadata("installed-files.txt")
    except FileNotFoundError:
        return None
    paths = (p for p in text.splitlines(keepends=False) if p)
    root = dist.location
    info = dist.egg_info
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


def _get_libpython_path(pid: int) -> Optional[str]:
    libpython_maps_pattern = re.compile(r"(?<=\s)/\S*libpython\S*\.so(\.\S+)?\Z")
    with open(f"/proc/{pid}/maps") as f:
        for line in f.readlines():
            match = libpython_maps_pattern.search(line.strip())
            if match is not None:
                return match.group()
    return None


@functools.lru_cache(maxsize=128)
def _get_python_full_version(pid: int, short_version: str) -> Optional[str]:
    assert re.match(r"[23]\.\d\d?", short_version)

    bin_file = _get_libpython_path(pid) or f"/proc/{pid}/exe"
    full_version_string_pattern = re.compile(rb"(?<=\D)" + short_version.encode() + rb"\.\d\d?(?=\x00)")

    # Try to extract the version string from the binary
    with open(resolve_host_path(bin_file, pid), "rb") as f:
        for line in f.readlines():
            match = full_version_string_pattern.search(line)
            if match is not None:
                return match.group().decode()
    return None


def _get_standard_libs_version(result: Dict[str, Optional[Tuple[str, str]]], pid: int):
    # Standard library modules are identified by being under a pythonx.y dir and *not* under site/dist-packages
    standard_lib_pattern = re.compile(r"/python(?P<version>\d\.\d\d?)/(?!.*(site|dist)-packages)")
    py_version = None

    for path in result:
        match = standard_lib_pattern.search(path)
        if match is not None:
            if py_version is None:
                try:
                    py_version = _get_python_full_version(pid, match.group("version"))
                except OSError:
                    pass
                if py_version is None:
                    # No need to continue trying if we failed
                    return None
            result[path] = ("standard-library", py_version)  # type: ignore


@functools.lru_cache(maxsize=128)
def _get_dists_files(packages_path: str) -> Dict[str, pkg_resources.Distribution]:
    """Return a dict of filename: dist for the distributions in packages_path"""
    path_to_dist = {}
    for dist in pkg_resources.find_distributions(packages_path):
        files_iter = _files_from_record(dist) or _files_from_legacy(dist)
        if files_iter is not None:
            path_to_dist.update(dict.fromkeys((os.path.join(packages_path, file) for file in files_iter), dist))
    return path_to_dist


_warned_no__normalized_cached = False


def _get_packages_versions(result: Dict[str, Optional[Tuple[str, str]]], pid: int):
    # A little monkey patch to prevent pkg_resources from converting "/proc/{pid}/root/" to "/".
    # This function resolves symlinks and makes paths absolute for comparison purposes which isn't required
    # for our usage.
    if hasattr(pkg_resources, "_normalize_cached"):
        original__normalize_cache = pkg_resources._normalize_cached
        pkg_resources._normalize_cached = lambda path: path
    else:
        global _warned_no__normalized_cached
        if not _warned_no__normalized_cached:
            # Log only once so we don't spam the log
            logger.warning("Cannot get modules version, pkg_resources has no '_normalize_cached' attribute")
            _warned_no__normalized_cached = True

        # Not much that we can do, pkg_resources.find_distributions won't work properly
        return result

    for path in result:
        if not path.startswith("/"):
            continue

        packages_path = _get_packages_dir(path)
        if packages_path is None:
            # This module is (probably) not part of a package
            continue
        packages_path = resolve_host_path(packages_path, pid)

        # Make sure to catch any exception. If something goes wrong just don't get the version, we shouldn't
        # interfere with gProfiler
        try:
            path_to_dist = _get_dists_files(packages_path)
        except Exception:
            continue

        dist_info = path_to_dist.get(resolve_host_path(path, pid))
        if dist_info is not None:
            name = _get_package_name(dist_info)
            if name is not None:
                result[path] = (name, dist_info.version)

    # Don't forget to restore the original implementation in case someone else uses this function
    pkg_resources._normalize_cached = original__normalize_cache
    return result


def get_modules_versions(modules_paths: Iterator[str], pid: int):
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
    _get_standard_libs_version(result, pid)
    _get_packages_versions(result, pid)
    return result
