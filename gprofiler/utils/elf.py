#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import hashlib
import struct
from typing import Optional, cast

from elftools.elf.elffile import ELFFile  # type: ignore
from elftools.elf.sections import NoteSection  # type: ignore
from psutil import NoSuchProcess, Process


def get_elf_buildid(path: str) -> Optional[str]:
    """
    Gets the build ID embedded in an ELF file section as a hex string,
    or None if not present.
    """
    with open(path, "rb") as f:
        elf = ELFFile(f)
        build_id_section = elf.get_section_by_name(".note.gnu.build-id")
        if build_id_section is None or not isinstance(build_id_section, NoteSection):
            return None

        for note in build_id_section.iter_notes():
            if note.n_type == "NT_GNU_BUILD_ID":
                return cast(str, note.n_desc)
        else:
            return None


def get_elf_id(path: str) -> str:
    """
    Gets an identifier for this ELF.
    We prefer to use buildids. If a buildid does not exist for an ELF file,
    we instead grab its SHA1.
    """
    buildid = get_elf_buildid(path)
    if buildid is not None:
        return f"buildid:{buildid}"

    # hash in one chunk
    with open(path, "rb") as f:
        return f"sha1:{hashlib.sha1(f.read()).hexdigest()}"


def get_mapped_dso_elf_id(process: Process, dso_part: str) -> Optional[str]:
    """
    Searches for a DSO path containing "dso_part" and gets its elfid.
    Returns None if not found.
    """
    for m in process.memory_maps():
        if dso_part in m.path:
            # don't need resolve_proc_root_links here - paths in /proc/pid/maps are normalized.
            return get_elf_id(f"/proc/{process.pid}/root/{m.path}")
    else:
        return None


_AUXV_ENTRY = struct.Struct("LL")

AT_EXECFN = 31
PATH_MAX = 4096


def _read_process_auxv(process: Process, auxv_id: int) -> int:
    try:
        with open(f"/proc/{process.pid}/auxv", "rb") as f:
            auxv = f.read()
    except FileNotFoundError:
        raise NoSuchProcess(process.pid)

    for i in range(0, len(auxv), _AUXV_ENTRY.size):
        entry = auxv[i : i + _AUXV_ENTRY.size]
        id_, val = _AUXV_ENTRY.unpack(entry)

        if id_ == auxv_id:
            assert isinstance(val, int)  # mypy fails to understand
            return val
    else:
        raise ValueError(f"auxv id {auxv_id} was not found!")


def _read_process_memory(process: Process, addr: int, size: int) -> bytes:
    try:
        with open(f"/proc/{process.pid}/mem", "rb", buffering=0) as mem:
            mem.seek(addr)
            return mem.read(size)
    except FileNotFoundError:
        raise NoSuchProcess(process.pid)


def read_process_execfn(process: Process) -> str:
    # reads process AT_EXECFN
    addr = _read_process_auxv(process, AT_EXECFN)
    fn = _read_process_memory(process, addr, PATH_MAX)
    return fn[: fn.index(b"\0")].decode()
