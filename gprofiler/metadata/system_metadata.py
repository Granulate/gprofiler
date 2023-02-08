import array
import errno
import ipaddress
import os
import platform
import re
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict, Optional, Tuple, cast

import distro
import psutil
from granulate_utils.linux.ns import run_in_ns

from gprofiler.log import get_logger_adapter
from gprofiler.platform import is_linux, is_windows
from gprofiler.utils import is_pyinstaller, run_process

if is_linux():
    import fcntl
else:
    import netifaces

UNKNOWN_VALUE = "unknown"

logger = get_logger_adapter(__name__)
hostname: Optional[str] = None
RUN_MODE_TO_DEPLOYMENT_TYPE: Dict[str, str] = {
    "k8s": "k8s",
    "container": "containers",
    "standalone_executable": "instances",
    "local_python": "instances",
}


def get_libc_version() -> Tuple[str, str]:
    # platform.libc_ver fails for musl, sadly (produces empty results).
    # so we'll run "ldd --version" and extract the version string from it.
    # not passing "encoding"/"text" - this runs in a different mount namespace, and Python fails to
    # load the files it needs for those encodings (getting LookupError: unknown encoding: ascii)
    def decode_libc_version(version: bytes) -> str:
        return version.decode("utf-8", errors="replace")

    try:
        ldd_version = run_process(
            ["ldd", "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, suppress_log=True, check=False
        ).stdout
    except FileNotFoundError:
        ldd_version = b"ldd not found"
    # catches GLIBC & EGLIBC
    m = re.search(rb"GLIBC (.*?)\)", ldd_version)
    if m is not None:
        return "glibc", decode_libc_version(m.group(1))
    # catches GNU libc
    m = re.search(rb"\(GNU libc\) (.*?)\n", ldd_version)
    if m is not None:
        return "glibc", decode_libc_version(m.group(1))
    # musl
    m = re.search(rb"musl libc.*?\nVersion (.*?)\n", ldd_version, re.M)
    if m is not None:
        return "musl", decode_libc_version(m.group(1))

    return UNKNOWN_VALUE, decode_libc_version(ldd_version)


def is_container() -> bool:
    return os.getenv("GPROFILER_IN_CONTAINER") is not None  # set by our Dockerfile


def get_run_mode() -> str:
    if os.getenv("GPROFILER_IN_K8S") is not None:  # set in k8s/gprofiler.yaml
        return "k8s"
    elif is_container():
        return "container"
    elif is_pyinstaller():
        return "standalone_executable"
    else:
        return "local_python"


def get_deployment_type(run_mode: str) -> str:
    return RUN_MODE_TO_DEPLOYMENT_TYPE.get(run_mode, UNKNOWN_VALUE)


def get_local_ip() -> str:
    # Fetches the local IP. Attempts to fetch it by seeing which local IP is used to connect to Google's DNS
    # servers (8.8.8.8). No packet will be sent.
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        return cast(str, s.getsockname()[0])
    except socket.error:
        return UNKNOWN_VALUE
    finally:
        s.close()


def get_mac_address() -> str:
    """
    Gets the MAC address of the first non-loopback interface.
    """
    if is_windows():
        mac_address, _ = get_windows_network_details()
        return mac_address

    assert sys.maxsize > 2**32, "expected to run on 64-bit!"
    SIZE_OF_STUCT_ifreq = 40  # correct for 64-bit

    IFNAMSIZ = 16
    IFF_LOOPBACK = 8
    MAC_BYTES_LEN = 6
    SIZE_OF_SHORT = struct.calcsize("H")
    MAX_BYTE_COUNT = 4096

    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_IP)

    # run SIOCGIFCONF to get all interface names
    buf = array.array("B", b"\0" * MAX_BYTE_COUNT)
    ifconf = struct.pack("iL", MAX_BYTE_COUNT, buf.buffer_info()[0])
    outbytes = struct.unpack("iL", fcntl.ioctl(s.fileno(), 0x8912, ifconf))[0]  # SIOCGIFCONF
    data = buf.tobytes()[:outbytes]
    for index in range(0, len(data), SIZE_OF_STUCT_ifreq):
        iface = data[index : index + SIZE_OF_STUCT_ifreq]

        # iface is now a struct ifreq which starts with the interface name.
        # we can use it for further calls.
        res = fcntl.ioctl(s.fileno(), 0x8913, iface)  # SIOCGIFFLAGS
        ifr_flags = struct.unpack(f"{IFNAMSIZ}sH", res[: IFNAMSIZ + SIZE_OF_SHORT])[1]
        if ifr_flags & IFF_LOOPBACK:
            continue

        # okay, not loopback, get its MAC address.
        res = fcntl.ioctl(s.fileno(), 0x8927, iface)  # SIOCGIFHWADDR
        address_bytes = struct.unpack(f"{IFNAMSIZ}sH{MAC_BYTES_LEN}s", res[: IFNAMSIZ + SIZE_OF_SHORT + MAC_BYTES_LEN])[
            2
        ]
        mac = struct.unpack(f"{MAC_BYTES_LEN}B", address_bytes)
        address = ":".join(["%02X" % i for i in mac])
        return address

    return UNKNOWN_VALUE


def get_cpu_info() -> Tuple[str, str]:
    """
    Parse /proc/cpuinfo to get model name & flags.
    """
    try:
        if is_windows():
            return UNKNOWN_VALUE, UNKNOWN_VALUE

        with open("/proc/cpuinfo") as f:
            model_names = []
            flags = []
            for line in f:
                m = re.match(r"^((?:model name)|(?:flags)|(?:Features))[ \t]*: (.*)$", line)
                if m is not None:
                    field, value = m.groups()
                    if field == "model name":
                        model_names.append(value)
                    else:
                        # flags in x86_64, Features in aarch64
                        assert field in ("flags", "Features"), f"unexpected field: {field!r}"
                        flags.append(value)

        if len(set(model_names)) > 1:
            logger.warning(f"CPU model names differ between cores, reporting only the first: {model_names}")

        if len(set(flags)) > 1:
            logger.warning(f"CPU flags differ between cores, reporting only the first: {model_names}")

        return model_names[0] if len(model_names) else UNKNOWN_VALUE, flags[0] if len(flags) else UNKNOWN_VALUE
    except Exception:
        logger.exception("Failed to get CPU model name & flags, reporting unknown")
        return UNKNOWN_VALUE, UNKNOWN_VALUE


@dataclass
class SystemInfo:
    python_version: str
    run_mode: str
    deployment_type: str
    kernel_release: str
    kernel_version: str
    system_name: str
    processors: int
    cpu_model_name: str
    cpu_flags: str
    memory_capacity_mb: int
    hostname: str
    system: str
    os_name: str
    os_release: str
    os_codename: str
    libc_type: str
    libc_version: str
    hardware_type: str
    pid: int
    mac_address: str
    private_ip: str
    spawn_uptime_ms: float


if is_windows():

    def get_windows_network_details() -> Tuple[str, str]:
        LOOPBACK = "127.0.0.1"
        ADDR_KEY = "addr"
        MAC_KEY = -1000
        for iface in netifaces.interfaces():
            mac_address = None
            for key, addresses in netifaces.ifaddresses(iface).items():
                for address in addresses:
                    if key == MAC_KEY:
                        mac_address = address[ADDR_KEY]
                        continue
                    try:
                        if ipaddress.IPv4Address(address[ADDR_KEY]) is not None and address[ADDR_KEY] != LOOPBACK:
                            assert isinstance(mac_address, str)
                            assert isinstance(address[ADDR_KEY], str)
                            return (mac_address, address[ADDR_KEY])
                    except ipaddress.AddressValueError:
                        pass
        return UNKNOWN_VALUE, UNKNOWN_VALUE


def get_static_system_info() -> SystemInfo:
    if is_windows():
        hostname, distribution, libc_tuple, mac_address, local_ip = _initialize_system_info_windows()
        spawn_uptime_ms = time.monotonic() * 1000
    else:
        hostname, distribution, libc_tuple, mac_address, local_ip = _initialize_system_info()
        clock = getattr(time, "CLOCK_BOOTTIME", time.CLOCK_MONOTONIC)
        try:
            spawn_uptime_ms = time.clock_gettime(clock)
        except OSError as error:
            if error.errno != errno.EINVAL:
                raise
            spawn_uptime_ms = time.clock_gettime(time.CLOCK_MONOTONIC)
    libc_type, libc_version = libc_tuple
    os_name, os_release, os_codename = distribution
    uname = platform.uname()
    cpu_count = os.cpu_count() or 0
    run_mode = get_run_mode()
    deployment_type = get_deployment_type(run_mode)
    cpu_model_name, cpu_flags = get_cpu_info()
    return SystemInfo(
        python_version=sys.version,
        run_mode=run_mode,
        deployment_type=deployment_type,
        kernel_release=uname.release,
        kernel_version=uname.version,
        system_name=uname.system,
        processors=cpu_count,
        cpu_model_name=cpu_model_name,
        cpu_flags=cpu_flags,
        memory_capacity_mb=round(psutil.virtual_memory().total / 1024 / 1024),  # type: ignore # virtual_memory doesn't
        # have a return type is types-psutil
        hostname=hostname,
        system=platform.system(),
        os_name=os_name,
        os_release=os_release,
        os_codename=os_codename,
        libc_type=libc_type,
        libc_version=libc_version,
        hardware_type=uname.machine,
        pid=os.getpid(),
        mac_address=mac_address,
        private_ip=local_ip,  # We want to stay consistent with our backend names
        spawn_uptime_ms=spawn_uptime_ms,
    )


def get_hostname() -> str:
    assert hostname is not None, "hostname not initialized!"
    return hostname


def get_hostname_or_none() -> Optional[str]:
    """
    Can be used early, possibly before hostname was initialized, and will return None if not initialized.
    """
    return hostname


def _initialize_system_info_windows() -> Any:
    global hostname
    hostname = f"<{UNKNOWN_VALUE}>"
    distribution = (UNKNOWN_VALUE, UNKNOWN_VALUE, UNKNOWN_VALUE)
    libc_tuple = (UNKNOWN_VALUE, UNKNOWN_VALUE)
    mac_address = UNKNOWN_VALUE
    local_ip = UNKNOWN_VALUE

    try:
        hostname = socket.gethostname()
    except Exception:
        logger.exception("Failed to get hostname")
    try:
        distribution = platform.system(), platform.release(), platform.version()
    except Exception:
        logger.exception("Failed to get distribution")
    try:
        libc_tuple = platform.libc_ver()
    except Exception:
        logger.exception("Failed to get libc version")
    try:
        mac_address, local_ip = get_windows_network_details()
    except Exception:
        logger.exception("Failed to get mac address and local ip")

    return hostname, distribution, libc_tuple, mac_address, local_ip


def _initialize_system_info() -> Any:
    # initialized first
    global hostname
    hostname = f"<{UNKNOWN_VALUE}>"  # < > are added to further distinct it from a legit hostname
    distribution = (UNKNOWN_VALUE, UNKNOWN_VALUE, UNKNOWN_VALUE)
    libc_version = (UNKNOWN_VALUE, UNKNOWN_VALUE)
    mac_address = UNKNOWN_VALUE
    local_ip = UNKNOWN_VALUE

    # move to host mount NS for distro & ldd.
    # now, distro will read the files on host.
    # also move to host UTS NS for the hostname.
    def get_infos() -> Any:
        nonlocal distribution, libc_version, mac_address, local_ip
        global hostname

        try:
            distribution = distro.linux_distribution()
        except Exception:
            logger.exception("Failed to get distribution")

        try:
            libc_version = get_libc_version()
        except Exception:
            logger.exception("Failed to get libc version")

        try:
            hostname = socket.gethostname()
        except Exception:
            logger.exception("Failed to get hostname")

        try:
            mac_address = get_mac_address()
        except Exception:
            logger.exception("Failed to get MAC address")

        try:
            local_ip = get_local_ip()
        except Exception:
            logger.exception("Failed to get the local IP")

    run_in_ns(["mnt", "uts", "net"], get_infos)

    return hostname, distribution, libc_version, mac_address, local_ip


@lru_cache(maxsize=None)
def get_arch() -> str:
    return platform.uname().machine
