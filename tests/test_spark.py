#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import logging
from time import sleep
from typing import List

import pytest
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

SPARK_MASTER_HOST = "127.0.0.1"


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


@pytest.fixture(scope="function")
def sparkpi_container(docker_client: DockerClient, application_docker_mounts: List[Mount]) -> Container:
    # Build the docker image that runs SparkPi
    spark_image = _build_image(docker_client=docker_client, runtime="spark")
    # TODO hostname = gethostname()
    container = docker_client.containers.run(
        spark_image,
        detach=True,
        mounts=application_docker_mounts,
        network_mode="host",
        pid_mode="host",
        environment={"SPARK_MASTER_HOST": SPARK_MASTER_HOST},
    )
    _wait_container_to_start(container)
    try:
        # We're sleeping to make sure SparkPi application is submitted and recognized by Master.
        sleep(15)
        yield container
    finally:
        container.stop()
        container.remove()


def test_sa_spark_discovered_mode(caplog: LogCaptureFixture, sparkpi_container: Container) -> None:
    """
    This test is an integration test that runs a SparkPi application and validates `discover()` and `snapshot()` API's
    of BigDataSampler works as expected in spark SA mode.
    We do so by building the image that in `containers/spark/Dockerfile`.
    The container hosts Master (no Workers) and runs the SparkPi application.
    """
    caplog.set_level(logging.DEBUG)
    sampler = BigDataSampler(logger, "", None, None, False)
    assert sampler.discover(), "BigDataSampler discover() failed to in discover mode"
    snapshot = sampler.snapshot()
    assert snapshot is not None, "BigDataSampler snapshot() failed to collect metrics"
    _validate_sa_metricssnapshot(snapshot)


def test_sa_spark_configured_mode(caplog: LogCaptureFixture, sparkpi_container: Container) -> None:
    caplog.set_level(logging.DEBUG)
    sampler = BigDataSampler(logger, "", f"spark://{SPARK_MASTER_HOST}:7077", "standalone", True)
    assert sampler.discover(), "discover() failed in configured mode"
    snapshot = sampler.snapshot()
    assert snapshot is not None, "snapshot() failed in configured mode"
    _validate_sa_metricssnapshot(snapshot)
