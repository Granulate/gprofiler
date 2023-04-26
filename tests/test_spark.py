#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import logging
from socket import gethostname
from time import sleep
from typing import List

from docker import DockerClient
from docker.models.containers import Container
from docker.types import Mount
from granulate_utils.metrics import MetricsSnapshot
from granulate_utils.metrics.metrics import (
    SPARK_AGGREGATED_STAGE_METRICS,
    SPARK_APPLICATION_DIFF_METRICS,
    SPARK_APPLICATION_GAUGE_METRICS,
    SPARK_EXECUTORS_METRICS,
)
from granulate_utils.metrics.sampler import BigDataSampler
from pytest import LogCaptureFixture

from gprofiler.log import get_logger_adapter
from tests.conftest import _build_image

logger = get_logger_adapter("gprofiler_test")

# List that includes all the metrics that are expected to be collected by `BigDataSampler`.
EXPECTED_SA_METRICS_KEYS = [
    metric
    for metrics_dict in (
        SPARK_APPLICATION_GAUGE_METRICS,
        SPARK_APPLICATION_DIFF_METRICS,
        SPARK_AGGREGATED_STAGE_METRICS,
        SPARK_EXECUTORS_METRICS,
    )
    for metric in metrics_dict.values()
]


def _wait_container_to_start(container: Container) -> None:
    while container.status != "running":
        if container.status == "exited":
            raise Exception(container.logs().decode())
        sleep(1)
        container.reload()


def _validate_sa_metricssnapshot(snapshot: MetricsSnapshot) -> None:
    """
    Validates that the snapshot contains all the expected metrics.
    """
    samples = snapshot.samples
    assert len(samples) != 0, "No samples found in snapshot"
    metric_keys = [sample.name for sample in samples]
    for key in EXPECTED_SA_METRICS_KEYS:
        assert key in metric_keys, f"Metric {key} not found in snapshot"


def test_sa_spark_discovery(
    docker_client: DockerClient, application_docker_mounts: List[Mount], caplog: LogCaptureFixture
) -> None:
    """
    This test is an integration test that runs a SparkPi application and validates `discover()` and `snapshot()` API's
    of BigDataSampler works as expected in spark SA mode.
    We do so by building the image that in `containers/spark/Dockerfile`.
    The container hosts Master (no Workers) and runs the SparkPi application.
    """
    # Creating a logger because BigDataSampler requires one
    caplog.set_level(logging.DEBUG)
    # Build the docker image that runs SparkPi
    spark_image = _build_image(docker_client=docker_client, runtime="spark")
    hostname = gethostname()
    container = docker_client.containers.run(
        spark_image, detach=True, mounts=application_docker_mounts, network_mode="host", pid_mode="host"
    )
    _wait_container_to_start(container)
    sampler = BigDataSampler(logger, hostname, None, None, False)

    discovered = sampler.discover()
    assert discovered, "BigDataSampler discover() failed to discover"
    # We're sleeping to make sure SparkPi application is up.
    sleep(15)
    snapshot = sampler.snapshot()
    assert snapshot is not None, "BigDataSampler snapshot() failed to collect metrics"
    _validate_sa_metricssnapshot(snapshot)
    container.stop()