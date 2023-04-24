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
from granulate_utils.metrics.sampler import BigDataSampler
from pytest import LogCaptureFixture

from gprofiler.log import get_logger_adapter
from tests.conftest import _build_image

logger = get_logger_adapter("gprofiler_test")

# List that includes all the metrics that are expected to be collected by the BigDataSampler, without their values.
EXPECTED_SA_METRICS_KEYS = [
    "spark_job_diff_numTasks",
    "spark_job_diff_numCompletedTasks",
    "spark_job_diff_numSkippedTasks",
    "spark_job_diff_numFailedTasks",
    "spark_job_diff_numFailedStages",
    "spark_job_numActiveTasks",
    "spark_job_numActiveStages",
    "spark_aggregated_stage_failed_tasks",
    "spark_aggregated_stage_active_tasks",
    "spark_aggregated_stage_pending_stages",
    "spark_aggregated_stage_failed_stages",
    "spark_aggregated_stage_active_stages",
    "spark_executors_count",
    "spark_executors_active_count",
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
    of BigDataSampler works as expected.
    We do so by building the image that in `containers/spark/Dockerfile`.
    The docker image hosts Master (no workers) and runs the SparkPi application.
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
    assert isinstance(snapshot, MetricsSnapshot), "BigDataSampler snapshot() failed to snapshot"
    _validate_sa_metricssnapshot(snapshot)

    container.stop()
