#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
# (C) Datadog, Inc. 2018-present. All rights reserved.
# Licensed under a 3-clause BSD style license (see LICENSE.bsd3).
#
import re
from typing import Any, Dict, Iterable, List, Tuple, Union
from urllib.parse import urljoin, urlparse

import requests

from gprofiler.log import get_logger_adapter
from gprofiler.metrics import Sample
from gprofiler.spark.metrics import (
    SPARK_APPLICATION_DIFF_METRICS,
    SPARK_APPLICATION_GAUGE_METRICS,
    SPARK_EXECUTORS_METRICS,
    SPARK_RDD_METRICS,
    SPARK_STAGE_METRICS,
    SPARK_STREAMING_BATCHES_METRICS,
    SPARK_STREAMING_STATISTICS_METRICS,
    SPARK_STRUCTURED_STREAMING_METRICS,
    SPARK_TASK_SUMMARY_METRICS,
    YARN_CLUSTER_METRICS,
    YARN_NODES_METRICS,
)
from gprofiler.spark.mode import SPARK_DRIVER_MODE, SPARK_MESOS_MODE, SPARK_YARN_MODE

# Application type and states to collect
YARN_SPARK_APPLICATION_SPECIFIER = "SPARK"
YARN_RUNNING_APPLICATION_SPECIFIER = "RUNNING"

# COMMON urls
YARN_APPS_PATH = "ws/v1/cluster/apps"
YARN_CLUSTER_PATH = "ws/v1/cluster/metrics"
YARN_NODES_PATH = "ws/v1/cluster/nodes"
SPARK_APPS_PATH = "api/v1/applications"
MESOS_MASTER_APP_PATH = "/frameworks"

STRUCTURED_STREAMS_METRICS_REGEX = re.compile(
    r"^[\w-]+\.driver\.spark\.streaming\.(?P<query_name>[\w-]+)\.(?P<metric_name>[\w-]+)$"
)

logger = get_logger_adapter(__name__)


class SparkCollector:
    def __init__(
        self,
        cluster_mode: str,
        master_address: str,
        *,
        cluster_metrics: bool = True,
        applications_metrics: bool = False,
        streaming_metrics: bool = False,
    ) -> None:
        self._cluster_mode = cluster_mode
        self._master_address = f"http://{master_address}"
        self._cluster_metrics = cluster_metrics
        self._applications_metrics = applications_metrics
        self._streaming_metrics = streaming_metrics
        self._task_summary_metrics = True
        self._last_iteration_app_job_metrics: Dict[str, Dict[str, Any]] = {}

    def collect(self) -> Iterable[Sample]:
        try:
            if self._cluster_metrics:
                if self._cluster_mode == SPARK_YARN_MODE:
                    yield from self._yarn_cluster_metrics()
                    yield from self._yarn_nodes_metrics()
                elif self._cluster_mode == SPARK_DRIVER_MODE:
                    pass

            if self._applications_metrics:
                spark_apps = self._get_running_apps()
                yield from self._spark_application_metrics(spark_apps)
                yield from self._spark_stage_metrics(spark_apps)
                yield from self._spark_executor_metrics(spark_apps)
                if self._streaming_metrics:
                    yield from self._spark_batches_streams_metrics(spark_apps)
                    yield from self._spark_streaming_statistics_metrics(spark_apps)
                    yield from self._spark_structured_streams_metrics(spark_apps)

                # Get the rdd metrics
                yield from self._spark_rdd_metrics(spark_apps)

            logger.debug("Succeeded gathering spark metrics")
        except Exception:
            logger.exception("Error while trying collect spark metrics")

    def _yarn_cluster_metrics(self) -> Iterable[Sample]:
        try:
            metrics_json = self._rest_request_to_json(self._master_address, YARN_CLUSTER_PATH)

            if metrics_json.get("clusterMetrics") is not None:
                yield from self._samples_from_json({}, metrics_json["clusterMetrics"], YARN_CLUSTER_METRICS)
        except Exception:
            logger.exception("Could not gather yarn cluster metrics")

    def _yarn_nodes_metrics(self) -> Iterable[Sample]:
        try:
            metrics_json = self._rest_request_to_json(self._master_address, YARN_NODES_PATH, states="RUNNING")
            running_nodes = metrics_json.get("nodes", {}).get("node", {})
            for node in running_nodes:
                for metric, value in node.get("resourceUtilization", {}).items():
                    node[metric] = value  # this will create all relevant metrics under same dictionary

                labels = {"node_hostname": node["nodeHostName"]}
                yield from self._samples_from_json(labels, node, YARN_NODES_METRICS)
        except Exception:
            logger.exception("Could not gather yarn nodes metrics")

    def _spark_application_metrics(self, running_apps: Dict[str, Tuple[str, str]]) -> Iterable[Sample]:
        """
        Get metrics for each Spark job.
        """
        iteration_metrics: Dict[str, Dict[str, Any]] = {}
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "jobs")
                application_diff_aggregated_metrics = dict.fromkeys(SPARK_APPLICATION_DIFF_METRICS.keys(), 0)
                application_gauge_aggregated_metrics = dict.fromkeys(SPARK_APPLICATION_GAUGE_METRICS.keys(), 0)
                iteration_metrics[app_id] = {}
                for job in response:
                    iteration_metrics[app_id][job["jobId"]] = job
                    first_time_seen_job = job["jobId"] not in self._last_iteration_app_job_metrics.get(app_id, {})
                    # In order to keep track of an application's metrics, we want to accumulate the values across all
                    # jobs. If the values are numActiveTasks or numActiveStages - there's no problem as they're
                    # always up-to-date and can just be summed. In case of completed jobs, only the last 1000
                    # (configurable - spark.ui.retainedJobs) jobs will be saved and only their metrics will be sent to
                    # us. Older jobs will be deleted , hence we can get into a situation when an old job is deleted,
                    # and then the accumulated metric will get lower, and the diff will be negative. In order to solve
                    # that, we only accumulate the value of newly seen jobs or the diff from the last time the value
                    # was seen.
                    for metric in SPARK_APPLICATION_DIFF_METRICS.keys():
                        if first_time_seen_job:
                            application_diff_aggregated_metrics[metric] += int(job[metric])
                        else:
                            application_diff_aggregated_metrics[metric] += int(job[metric]) - int(
                                self._last_iteration_app_job_metrics[app_id][job["jobId"]][metric]
                            )

                    for metric in SPARK_APPLICATION_GAUGE_METRICS.keys():
                        application_gauge_aggregated_metrics[metric] += int(job[metric])

                labels = {"app_name": app_name, "app_id": app_id}
                yield from self._samples_from_json(
                    labels, application_diff_aggregated_metrics, SPARK_APPLICATION_DIFF_METRICS
                )
                yield from self._samples_from_json(
                    labels, application_gauge_aggregated_metrics, SPARK_APPLICATION_GAUGE_METRICS
                )

            except Exception:
                logger.exception("Could not gather spark jobs metrics")
        self._last_iteration_app_job_metrics = iteration_metrics

    def _spark_stage_metrics(self, running_apps: Dict[str, Tuple[str, str]]) -> Iterable[Sample]:
        """
        Get metrics for each Spark stage.
        """
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "stages")

                for stage in response:
                    status = stage.get("status")
                    stage_id = stage.get("stageId")
                    labels = {
                        "app_name": app_name,
                        "app_id": app_id,
                        "status": status.lower(),
                        "stage_id": stage_id,
                    }

                    yield from self._samples_from_json(labels, stage, SPARK_STAGE_METRICS)

                    if self._task_summary_metrics and status == "ACTIVE":
                        stage_response = self._rest_request_to_json(
                            base_url, SPARK_APPS_PATH, app_id, "stages", str(stage_id), details="false", status="ACTIVE"
                        )

                        for attempt in stage_response:
                            try:
                                tasks_summary_response = self._rest_request_to_json(
                                    base_url,
                                    SPARK_APPS_PATH,
                                    app_id,
                                    "stages",
                                    str(stage_id),
                                    str(attempt.get("attemptId")),
                                    "taskSummary",
                                    quantiles="0.5,0.75,0.99",
                                )

                                yield from self._task_summary_samples_from_json(
                                    labels, tasks_summary_response, SPARK_TASK_SUMMARY_METRICS
                                )
                            except Exception:
                                logger.exception("Could not gather spark tasks summary for stage. Skipped.")
            except Exception:
                logger.exception("Could not gather spark stages metrics")

    def _spark_executor_metrics(self, running_apps: Dict[str, Tuple[str, str]]) -> Iterable[Sample]:
        """
        Get metrics for each Spark executor.
        """
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                executors = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "executors")
                labels = {"app_name": app_name, "app_id": app_id}
                yield from self._samples_from_json(
                    labels,
                    {
                        "count": len(executors),
                        "activeCount": len([executor for executor in executors if executor["activeTasks"] > 0]),
                    },
                    SPARK_EXECUTORS_METRICS,
                )
            except Exception:
                logger.exception("Could not gather spark executors metrics")

    def _spark_rdd_metrics(self, running_apps: Dict[str, Tuple[str, str]]) -> Iterable[Sample]:
        """
        Get metrics for each Spark RDD.
        """

        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "storage/rdd")
                labels = {"app_name": app_name, "app_id": app_id}
                for rdd in response:
                    yield from self._samples_from_json(labels, rdd, SPARK_RDD_METRICS)
            except Exception:
                logger.exception("Could not gather Spark RDD metrics")

    def _spark_streaming_statistics_metrics(self, running_apps: Dict[str, Tuple[str, str]]) -> Iterable[Sample]:
        """
        Get metrics for each application streaming statistics.
        """
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "streaming/statistics")

                labels = {"app_name": app_name, "app_id": app_id}

                # NOTE: response is a dict
                yield from self._samples_from_json(labels, response, SPARK_STREAMING_STATISTICS_METRICS)
            except Exception:
                logger.exception("Could not gather Spark streaming metrics")

    @staticmethod
    def _get_last_batches_metrics(
        batches: List[Dict[str, Union[str, int]]], completed_batches: List[Dict[str, Union[str, int]]], n: int
    ) -> Dict[str, float]:
        last = batches[:n]
        last_completed = completed_batches[:n]
        return {
            f"avg{n}_inputSize": sum([int(batch.get("inputSize", 0)) for batch in last]) / len(last),
            f"max{n}_inputSize": max([int(batch.get("inputSize", 0)) for batch in last]),
            f"avg{n}_processingTime": sum([int(batch.get("processingTime", 0)) for batch in last_completed])
            / len(last_completed),
            f"max{n}_processingTime": max([int(batch.get("processingTime", 0)) for batch in last_completed]),
            f"avg{n}_totalDelay": sum([int(batch.get("totalDelay", 0)) for batch in last_completed])
            / len(last_completed),
            f"max{n}_totalDelay": max([int(batch.get("totalDelay", 0)) for batch in last_completed]),
            f"avg{n}_batchDuration": sum([int(batch.get("batchDuration", 0)) for batch in last]) / len(last),
        }

    def _spark_batches_streams_metrics(self, running_apps: Dict[str, Tuple[str, str]]) -> Iterable[Sample]:
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                batches = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "/streaming/batches")
                completed_batches = list(
                    filter(
                        lambda batch: batch.get("batchId") is not None and batch.get("status") == "COMPLETED", batches
                    )
                )
                if batches:
                    labels = {
                        "app_name": app_name,
                        "app_id": app_id,
                        "batch_duration": batches[0].get("batchDuration"),
                    }
                    batch_metrics = {
                        "last_inputSize": batches[0].get("inputSize"),
                        "last_processingTime": completed_batches[0].get("processingTime"),
                        "last_totalDelay": completed_batches[0].get("totalDelay"),
                        "last_batchDuration": completed_batches[0].get("batchDuration"),
                    }
                    batch_metrics.update(self._get_last_batches_metrics(batches, completed_batches, 3))
                    batch_metrics.update(self._get_last_batches_metrics(batches, completed_batches, 10))
                    batch_metrics.update(self._get_last_batches_metrics(batches, completed_batches, 25))
                    yield from self._samples_from_json(labels, batch_metrics, SPARK_STREAMING_BATCHES_METRICS)
            except Exception:
                logger.exception("Could not gather Spark batch metrics for application")

    def _spark_structured_streams_metrics(self, running_apps: Dict[str, Tuple[str, str]]) -> Iterable[Sample]:
        """
        Get metrics for each application structured stream.
        Requires:
        - The Metric Servlet to be enabled to path <APP_URL>/metrics/json (enabled by default)
        - `SET spark.sql.streaming.metricsEnabled=true` in the app
        """
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, "/metrics/json")
                response = {
                    metric_name: v["value"]
                    for metric_name, v in response.get("gauges", {}).items()
                    if "streaming" in metric_name and "value" in v
                }
                labels = {"app_name": app_name, "app_id": app_id}
                for gauge_name, value in response.items():
                    if match := STRUCTURED_STREAMS_METRICS_REGEX.match(gauge_name):
                        groups = match.groupdict()
                        metric_name = groups["metric_name"]
                        if metric := SPARK_STRUCTURED_STREAMING_METRICS.get(metric_name):
                            assert value is not None, f"unexpected null value for metric {metric}!"
                            yield Sample(labels, metric, value)
                        else:
                            logger.debug("Unknown metric_name encountered: '%s'", str(metric_name))
            except Exception:
                logger.exception("Could not gather structured streaming metrics for application")

    @staticmethod
    def _task_summary_samples_from_json(
        labels: Dict[str, str], response_json: Dict[str, List[int]], metrics: Dict[str, str]
    ) -> Iterable[Sample]:
        quantile_index = 0
        if response_json is None:
            return
        quantiles_list = response_json.get("quantiles")
        if not quantiles_list:
            return

        for quantile in quantiles_list:
            for status, metric in metrics.items():
                metric_status = response_json.get(status)
                if metric_status and metric_status[quantile_index] is not None:
                    yield Sample({**labels, "quantile": str(quantile)}, metric, metric_status[quantile_index])

            quantile_index += 1

    @staticmethod
    def _samples_from_json(
        labels: Dict[str, str], response_json: Dict[Any, Any], metrics: Dict[str, str]
    ) -> Iterable[Sample]:
        """
        Parse the JSON response and set the metrics
        """
        if response_json is None:
            return

        for field_name, metric_name in metrics.items():
            if (value := response_json.get(field_name)) is not None:
                yield Sample(labels, metric_name, value)

    def _get_running_apps(self) -> Dict[str, Tuple[str, str]]:
        """
        Determine what mode was specified
        """
        if self._cluster_mode == SPARK_YARN_MODE:
            running_apps = self._yarn_init()
            return self._get_spark_app_ids(running_apps)
        elif self._cluster_mode == SPARK_DRIVER_MODE:
            return self._driver_init()
        elif self._cluster_mode == SPARK_MESOS_MODE:
            return self._mesos_init()
        else:
            raise ValueError(f"Invalid cluster mode {self._cluster_mode!r}")

    def _driver_init(self) -> Dict[str, Tuple[str, str]]:
        """
        Return a dictionary of {app_id: (app_name, tracking_url)} for the running Spark applications
        """
        return self._driver_get_apps(status=YARN_RUNNING_APPLICATION_SPECIFIER)

    def _driver_get_apps(self, *args: Any, **kwargs: Any) -> Dict[str, Tuple[str, str]]:
        """
        Return a dictionary of {app_id: (app_name, tracking_url)} for the Spark applications
        """
        app_list = {}
        metrics_json = self._rest_request_to_json(self._master_address, SPARK_APPS_PATH, *args, **kwargs)

        for app_json in metrics_json:
            app_id = str(app_json.get("id"))
            app_name = str(app_json.get("name"))
            app_list[app_id] = (app_name, self._master_address)

        return app_list

    def _yarn_init(self) -> Dict[str, Tuple[str, str]]:
        """
        Return a dictionary of {app_id: (app_name, tracking_url)} for running Spark applications.
        """
        return self._yarn_get_spark_apps(
            states=YARN_RUNNING_APPLICATION_SPECIFIER, applicationTypes=YARN_SPARK_APPLICATION_SPECIFIER
        )

    def _yarn_get_spark_apps(self, *args: Any, **kwargs: Any) -> Dict[str, Tuple[str, str]]:
        metrics_json = self._rest_request_to_json(self._master_address, YARN_APPS_PATH, *args, **kwargs)

        running_apps = {}

        if metrics_json.get("apps"):
            if metrics_json["apps"].get("app") is not None:

                for app_json in metrics_json["apps"]["app"]:
                    app_id = app_json.get("id")
                    tracking_url = app_json.get("trackingUrl")
                    app_name = app_json.get("name")

                    if app_id and tracking_url and app_name:
                        running_apps[app_id] = (app_name, tracking_url)

        return running_apps

    def _get_spark_app_ids(self, running_apps: Dict[str, Tuple[str, str]]) -> Dict[str, Tuple[str, str]]:
        """
        Traverses the Spark application master in YARN to get a Spark application ID.
        Return a dictionary of {app_id: (app_name, tracking_url)} for Spark applications
        """
        spark_apps = {}
        for app_id, (app_name, tracking_url) in running_apps.items():
            try:
                response = self._rest_request_to_json(tracking_url, SPARK_APPS_PATH)

                for app in response:
                    app_id = app.get("id")
                    app_name = app.get("name")

                    if app_id and app_name:
                        spark_apps[app_id] = (app_name, tracking_url)
            except Exception:
                logger.exception("Could not fetch data from url", url=tracking_url)

        return spark_apps

    def _mesos_init(self) -> Dict[str, Tuple[str, str]]:
        running_apps = {}
        metrics_json = self._rest_request_to_json(self._master_address, MESOS_MASTER_APP_PATH)
        for app_json in metrics_json.get("frameworks", []):
            app_id = app_json.get("id")
            tracking_url = app_json.get("webui_url")
            app_name = app_json.get("name")
            if app_id and tracking_url and app_name:
                running_apps[app_id] = (app_name, tracking_url)
        return running_apps

    def _rest_request(self, url: str, object_path: str, *args: Any, **kwargs: Any) -> requests.Response:
        """
        Query the given URL and return the response
        """
        if object_path:
            url = self._join_url_dir(url, object_path)

        # Add args to the url
        if args:
            for directory in args:
                url = self._join_url_dir(url, directory)

        logger.debug(f"Spark check URL: {url}")
        response = requests.get(url, params={k: v for k, v in kwargs.items() if v is not None}, timeout=3)
        response.raise_for_status()
        return response

    def _rest_request_to_json(self, address: str, object_path: str, *args: Any, **kwargs: Any) -> Any:
        """
        Query the given URL and return the JSON response
        """
        return self._rest_request(address, object_path, *args, **kwargs).json()

    def _get_request_url(self, url: str) -> str:
        """
        Get the request address, build with proxy if necessary
        """
        parsed = urlparse(url)

        _url = url
        if not (parsed.netloc and parsed.scheme):
            _url = urljoin(self._master_address, parsed.path)

        return _url

    @staticmethod
    def _join_url_dir(url: str, *args: Any) -> str:
        """
        Join a URL with multiple directories
        """
        for path in args:
            url = url.rstrip("/") + "/"
            url = urljoin(url, path.lstrip("/"))

        return url
