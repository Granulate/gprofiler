"""Package name and version for Python files.

Some of the functions in this module are implemented based on similar functions
in pip: _get_metadata, _convert_legacy_entry, _files_from_record,
_files_from_legacy, _get_package_name.
"""
import csv
import email
import os
import pathlib
from typing import Iterator, List, Optional, Tuple

import pkg_resources

# A little monkey patch to prevent pkg_resources from converting "/proc/{pid}/root/" to "/"
pkg_resources._normalize_cached = lambda path: path


__all__ = ["get_versions"]


def _convert_to_proc_root_path(path: str, pid: int) -> str:
    assert path.startswith("/")
    return os.path.join(f"/proc/{pid}/root", path)


def _get_packages_dir(file_path: str) -> Optional[str]:
    if not file_path.startswith("/"):
        return None

    idx = file_path.rfind("-packages/")
    if idx == -1 or (not file_path[:idx].endswith("site") and not file_path[:idx].endswit("dist")):
        return None

    return file_path[:idx] + "-packages/"


def _get_metadata(dist: pkg_resources.Distribution) -> email.message.Message:
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
    return feed_parser.close()


def _convert_legacy_entry(entry: Tuple[str, ...], info: Tuple[str, ...]) -> str:
    """Convert a legacy installed-files.txt path into modern RECORD path.

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
    try:
        text = dist.get_metadata("RECORD")
    except:
        return None
    # This extra Path-str cast normalizes entries.
    return (str(pathlib.Path(row[0])) for row in csv.reader(text.splitlines()))


def _files_from_legacy(dist: pkg_resources.Distribution) -> Optional[Iterator[str]]:
    try:
        text = dist.get_metadata("installed-files.txt")
    except:
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


def _get_package_name(dist: pkg_resources.Distribution) -> Optional(str):
    # TODO: Test
    metadata = _get_metadata(dist)
    if metadata:
        # The metadata should NEVER be missing the Name: key, but if it somehow
        # does, fall back to the known canonical name.
        return metadata.get("Name", dist.project_name)
    return None


def get_versions(modules_paths: List[str], pid: int):
    """Return a dict with module_path: (package_name, version). If couldn't
    determine the version the value is None.

    modules_paths must be absoulte. pid is required to access the path via
    /proc/{pid}/root/.

    Given a path to a module, it's not trivial to determine its package as some
    packages contain modules with completely unrelated names (e.g. the module
    pkg_resources used in this function is a part of the setuptools package).
    This function gathers info for all the packages in the (dist|site)-packages
    directory in each path, including a list of all the files associated with
    each package. This list is searched for the given module path.
    """
    result = dict.fromkeys(modules_paths)
    path_to_dist = {}

    for path in modules_paths:
        if not path.startswith("/"):
            continue

        if path not in path_to_dist:
            packages_path = _get_packages_dir(path)
            if packages_path is None:
                # This module is (probably) not part of a package
                continue
            packages_path = _convert_to_proc_root_path(packages_path, pid)

            for dist in pkg_resources.find_distributions(packages_path):
                files_iter = _files_from_record(dist) or _files_from_legacy(dist)
                if files_iter is not None:
                    path_to_dist.update(dict.fromkeys((os.path.join(packages_path, file) for file in files_iter), dist))

        dist = path_to_dist.get(_convert_to_proc_root_path(path, pid))
        if dist is not None:
            name = _get_package_name(dist)
            if name is not None:
                result[path] = (_get_package_name(dist), dist.version)

    return result
