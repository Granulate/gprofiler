#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

from socket import gethostname
from time import sleep
from typing import List

from conftest import _build_image
from docker import DockerClient
from docker.models.containers import Container
from docket.types import Mount
from granulate_utils.metrics.sampler import BigDataSampler
from pytest import LogCaptureFixture
import logging


def _wait_container_to_start(container: Container) -> None:
    while container.status != "running":
        if container.status == "exited":
            raise Exception(container.logs().decode())
        sleep(1)
        container.reload()


def test_spark_discovery(
    docker_client: DockerClient, application_docker_mounts: List[Mount], caplog: LogCaptureFixture
) -> None:
    # Build the docker image that runs SparkPi
    logger = logging.getLogger("test_spark_discovery")
    logger_adapter = logging.LoggerAdapter(logger, {'key': 'value'})
    caplog.set_level(logging.DEBUG)
    spark_image = _build_image(docker_client=docker_client, runtime="spark")
    hostname = gethostname()
    container = docker_client.containers.run(
        spark_image, detach=True, mounts=application_docker_mounts, ports={"0.0.0.0": 0}, hostname=hostname
    )
    _wait_container_to_start(container)
    # Technically, the hostname may not be relevant because the spark runs in a container.
    sampler = BigDataSampler(logger_adapter, hostname, None, None, False)

    discovered = sampler.discover()
    assert discovered, "BigDataSampler discover() failed to discover"
    snapshot = sampler.snapshot()
    assert not snapshot, "BigDataSampler snapshot() failed to snapshot"
