import os
import subprocess
from pathlib import Path
from typing import Dict, List, Mapping

from docker import DockerClient
from docker.models.images import Image


def run_privileged_container(
    docker_client: DockerClient,
    image: Image,
    command: List[str],
    volumes: Dict[str, Dict[str, str]] = None,
    **extra_kwargs,
):
    if volumes is None:
        volumes = {}
    container = docker_client.containers.run(
        image,
        command,
        privileged=True,
        network_mode="host",
        pid_mode="host",
        userns_mode="host",
        volumes=volumes,
        auto_remove=True,
        stderr=True,
        **extra_kwargs,
    )
    print(f"Container logs {container}")


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
