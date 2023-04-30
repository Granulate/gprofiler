#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import logging
from time import sleep
from typing import Dict, Tuple

import pytest
from docker import DockerClient
from docker.models.containers import Container
from granulate_utils.metrics import MetricsSnapshot
from granulate_utils.metrics.metrics import (
    SPARK_AGGREGATED_STAGE_METRICS,
    SPARK_APPLICATION_DIFF_METRICS,
    SPARK_APPLICATION_GAUGE_METRICS,
    SPARK_EXECUTORS_METRICS,
)
from granulate_utils.metrics.mode import SPARK_STANDALONE_MODE
from granulate_utils.metrics.sampler import BigDataSampler
from granulate_utils.metrics.spark import SparkRunningApps
from pytest import LogCaptureFixture

from gprofiler.log import get_logger_adapter
from tests.conftest import build_image
from tests.utils import wait_container_to_start

# `BigDataSampler` receives a logger as an argument.
logger = get_logger_adapter("gprofiler_spark_test")

# List that includes all the metrics that are expected to be collected by `BigDataSampler` in SA mode.
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

DISCOVER_INTERVAL_SECS = 5
DISCOVER_TIMEOUT_SECS = 60


@pytest.fixture
def sparkpi_container(docker_client: DockerClient) -> Container:
    """
    This fixture is responsible for running SparkPi application in a container.
    See `containers/spark/Dockerfile`
    """
    spark_image = build_image(docker_client=docker_client, runtime="spark")
    container = docker_client.containers.run(
        spark_image,
        detach=True,
        mounts=[],
        network_mode="host",
        pid_mode="host",
        environment={"SPARK_MASTER_HOST": SPARK_MASTER_HOST},
    )
    wait_container_to_start(container)
    try:
        yield container
    finally:
        container.stop()
        container.remove()


def _wait_for_sparkpi_to_start() -> None:
    """
    Waits for SparkPi to be recognized by Master.
    Doing so using `SparkRunningApps` class, `get_running_apps()` method.

    Because Master is provisioning, it takes a few seconds until we receive a response from it.
    """
    running_apps = SparkRunningApps(SPARK_STANDALONE_MODE, f"http://{SPARK_MASTER_HOST}:8080", logger)
    apps: Dict[str, Tuple[str, str]] = {}
    while not apps:
        try:
            apps = running_apps.get_running_apps()
        except requests.exceptions.ConnectionError as e:
            if 'Max retries exceeded' in str(e):
                # Spark Master is not ready yet.
                pass
            sleep(1)


def _validate_spark_sa_metricssnapshot(snapshot: MetricsSnapshot) -> None:
    """
    Validates that the snapshot contains all the expected metrics.
    """
    samples = snapshot.samples
    assert len(samples) != 0, "No samples found in snapshot"
    metric_keys = [sample.name for sample in samples]
    for key in EXPECTED_SA_METRICS_KEYS:
        assert key in metric_keys, f"Metric {key} not found in snapshot"


def test_spark_sa_discovered_mode(caplog: LogCaptureFixture, sparkpi_container: Container) -> None:
    """
    Validates `BigDataSampler`s' `discover()` and `snapshot()` API's in discover mode.
    In discover mode we do not know what's the cluster mode and master address.
    """
    discover_timeout = DISCOVER_TIMEOUT_SECS
    caplog.set_level(logging.DEBUG)
    sampler = BigDataSampler(logger, "", None, None, False)
    # We want to make the discovery process as close as possible to the real world scenario.
    while not (discovered := sampler.discover()) and discover_timeout > 0:
        sleep(DISCOVER_INTERVAL_SECS)
        discover_timeout -= DISCOVER_INTERVAL_SECS
    assert discovered, "Failed to discover cluster mode and master address"
    assert sampler._master_address == f"http://{SPARK_MASTER_HOST}:8080", "wrong master address was discovered"
    assert sampler._cluster_mode == SPARK_STANDALONE_MODE, "wrong cluster mode was discovered"
    # Need to wait for SparkPi to be recognized by Master.
    _wait_for_sparkpi_to_start()
    snapshot = sampler.snapshot()
    assert snapshot is not None, "BigDataSampler snapshot() failed to collect metrics"
    _validate_spark_sa_metricssnapshot(snapshot)
    assert any(
        "Guessed settings" in message for message in caplog.messages
    ), "guessed cluster log was not printed to log"


def test_spark_sa_configured_mode(caplog: LogCaptureFixture, sparkpi_container: Container) -> None:
    """
    Validates `BigDataSampler`s' `discover()` and `snapshot()` API's after manually configured `BigDataSampler` with
    cluster mode, master address and enabling Applications Metrics Collector.
    """
    caplog.set_level(logging.DEBUG)
    sampler = BigDataSampler(logger, "", f"{SPARK_MASTER_HOST}:8080", "standalone", True)
    # First call to `discover()` should return True, and print a debug log we later on validate.
    assert sampler.discover(), "discover() failed in configured mode"
    # Need to wait for SparkPi to be recognized by Master.
    _wait_for_sparkpi_to_start()
    snapshot = sampler.snapshot()
    assert snapshot is not None, "snapshot() failed in configured mode"
    _validate_spark_sa_metricssnapshot(snapshot)
    assert any(
        "No need to guess cluster mode and master address, manually configured" in message
        for message in caplog.messages
    ), "configured cluster log was not printed"
