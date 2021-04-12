from typing import List, Dict

from docker import DockerClient
from docker.models.images import Image


def run_privileged_container(docker_client: DockerClient, image: Image, command: List[str],
                             volumes: Dict[str, Dict[str, str]] = None, **extra_kwargs):
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
        **extra_kwargs
    )
    print(f"Container logs {container}")
