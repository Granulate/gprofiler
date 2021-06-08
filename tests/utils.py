import os
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Tuple

from docker import DockerClient
from docker.models.containers import Container
from docker.models.images import Image


def run_privileged_container(
    docker_client: DockerClient,
    image: Image,
    command: List[str],
    volumes: Dict[str, Dict[str, str]] = None,
    auto_remove=True,
    **extra_kwargs,
) -> Tuple[Optional[Container], str]:
    if volumes is None:
        volumes = {}
    container_or_logs = docker_client.containers.run(
        image,
        command,
        privileged=True,
        network_mode="host",
        pid_mode="host",
        userns_mode="host",
        volumes=volumes,
        auto_remove=auto_remove,
        stderr=True,
        **extra_kwargs,
    )
    if isinstance(container_or_logs, Container):
        container, logs = container_or_logs, container_or_logs.logs().decode()
    else:
        assert isinstance(container_or_logs, bytes), container_or_logs
        container, logs = None, container_or_logs.decode()

    print("Container logs:", logs.decode())  # print, so failing tests display it
    return container, logs.decode()


def copy_file_from_image(image: Image, container_path: str, host_path: str) -> None:
    os.makedirs(os.path.dirname(host_path), exist_ok=True)
    # I tried writing it with the docker-py API, but retrieving large files with container.get_archive() just hangs...
    subprocess.run(
        f"c=$(docker container create {image.id}) && "
        f"{{ docker cp $c:{container_path} {host_path}; ret=$?; docker rm $c > /dev/null; exit $ret; }}",
        shell=True,
        check=True,
    )


def chmod_path_parts(path: Path, add_mode: int) -> None:
    """
    Adds 'add_mode' to all parts in 'path'.
    """
    for i in range(1, len(path.parts)):
        subpath = os.path.join(*path.parts[:i])
        os.chmod(subpath, os.stat(subpath).st_mode | add_mode)


def assert_function_in_collapsed(function_name: str, collapsed: Mapping[str, int]) -> None:
    print(f"collapsed: {collapsed}")
    assert any(
        (function_name in record) for record in collapsed.keys()
    ), f"function {function_name!r} missing in collapsed data!"
