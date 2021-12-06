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
import os
import pathlib
from typing import Iterator, Optional, Tuple

# pkg_resources is a part of setuptools, but it has a standalone deprecated version. That's the version mypy
# is looking for, but the stubs there are extremely deprecated
import pkg_resources  # type: ignore

from gprofiler.log import get_logger_adapter
from gprofiler.utils import convert_to_proc_root_path

logger = get_logger_adapter(__name__)


__all__ = ["get_packages_versions"]


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
    # TODO: Test
    metadata = _get_metadata(dist)
    if metadata:
        # The metadata should NEVER be missing the Name: key, but if it somehow
        # does, fall back to the known canonical name.
        return metadata.get("Name", dist.project_name)
    return None


_warned_no__normalized_cached = False


def get_packages_versions(modules_paths: Iterator[str], pid: int):
    """Return a dict with module_path: (package_name, version). If couldn't
    determine the version the value is None.

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

    path_to_dist = {}

    for path in result:
        if not path.startswith("/"):
            continue

        if path not in path_to_dist:
            packages_path = _get_packages_dir(path)
            if packages_path is None:
                # This module is (probably) not part of a package
                continue
            packages_path = convert_to_proc_root_path(packages_path, pid)

            # Make sure to catch any exception. If something goes wrong just don't get the version, we shouldn't
            # interfere with gProfiler
            try:
                for dist in pkg_resources.find_distributions(packages_path):
                    files_iter = _files_from_record(dist) or _files_from_legacy(dist)
                    if files_iter is not None:
                        path_to_dist.update(
                            dict.fromkeys((os.path.join(packages_path, file) for file in files_iter), dist)
                        )
            except Exception:
                pass

        dist_info = path_to_dist.get(convert_to_proc_root_path(path, pid))
        if dist_info is not None:
            name = _get_package_name(dist_info)
            if name is not None:
                result[path] = (_get_package_name(dist_info), dist_info.version)

    # Don't forget to restore the original implementation in case someone else uses this function
    pkg_resources._normalize_cached = original__normalize_cache
    return result
