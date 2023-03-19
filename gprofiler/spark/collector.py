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
from bs4 import BeautifulSoup

from gprofiler.log import get_logger_adapter
from gprofiler.metrics import Sample
from gprofiler.spark.metrics import (
    SPARK_AGGREGATED_STAGE_METRICS,
    SPARK_APPLICATION_DIFF_METRICS,
    SPARK_APPLICATION_GAUGE_METRICS,
    SPARK_EXECUTORS_METRICS,
    SPARK_RUNNING_APPS_COUNT_METRIC,
    SPARK_STREAMING_BATCHES_METRICS,
    SPARK_STREAMING_STATISTICS_METRICS,
    SPARK_STRUCTURED_STREAMING_METRICS,
    YARN_CLUSTER_METRICS,
    YARN_NODES_METRICS,
)
from gprofiler.spark.mode import SPARK_MESOS_MODE, SPARK_STANDALONE_MODE, SPARK_YARN_MODE

# Application type and states to collect
YARN_SPARK_APPLICATION_SPECIFIER = "SPARK"
YARN_RUNNING_APPLICATION_SPECIFIER = "RUNNING"

SPARK_MASTER_STATE_PATH = "/json/"
SPARK_MASTER_APP_PATH = "/app/"

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
        self._last_iteration_app_job_metrics: Dict[str, Dict[str, Any]] = {}

    def collect(self) -> Iterable[Sample]:
        try:
            if self._cluster_metrics:
                if self._cluster_mode == SPARK_YARN_MODE:
                    yield from self._yarn_cluster_metrics()
                    yield from self._yarn_nodes_metrics()
                elif self._cluster_mode == SPARK_STANDALONE_MODE:
                    # Standalone mode will always need to collect the application metrics.
                    self._applications_metrics = True

            if self._applications_metrics:
                spark_apps = self._get_running_apps()
                yield from self._spark_application_metrics(spark_apps)
                yield from self._spark_stage_metrics(spark_apps)
                yield from self._spark_executor_metrics(spark_apps)
                yield from self._running_applications_count_metric(spark_apps)
                if self._streaming_metrics:
                    yield from self._spark_batches_streams_metrics(spark_apps)
                    yield from self._spark_streaming_statistics_metrics(spark_apps)
                    yield from self._spark_structured_streams_metrics(spark_apps)

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

    def _running_applications_count_metric(self, running_apps: Dict[str, Any]) -> Iterable[Sample]:
        yield Sample(name=SPARK_RUNNING_APPS_COUNT_METRIC, value=len(running_apps), labels={})

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
            labels = {"app_name": str(app_name), "app_id": str(app_id)}
            logger.debug("Gathering stage metrics for app", app_id=app_id)
            try:
                base_url = self._get_request_url(tracking_url)
                response = self._rest_request_to_json(base_url, SPARK_APPS_PATH, app_id, "stages")
                logger.debug("Got response for stage metrics for app %s", app_id)
            except Exception as e:
                logger.exception("Exception occurred while trying to retrieve stage metrics", extra={"exception": e})
                return

            aggregated_metrics = dict.fromkeys(SPARK_AGGREGATED_STAGE_METRICS.keys(), 0)
            for stage in response:
                curr_stage_status = stage["status"]
                aggregated_metrics["failed_tasks"] += stage["numFailedTasks"]
                if curr_stage_status == "PENDING":
                    aggregated_metrics["pending_stages"] += 1
                elif curr_stage_status == "ACTIVE":
                    aggregated_metrics["active_tasks"] += stage["numActiveTasks"]
                    aggregated_metrics["active_stages"] += 1
                elif curr_stage_status == "FAILED":
                    aggregated_metrics["failed_stages"] += 1
            yield from self._samples_from_json(labels, aggregated_metrics, SPARK_AGGREGATED_STAGE_METRICS)

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
                        "count": len(executors) - 1,  # -1 for the driver
                        "activeCount": len([executor for executor in executors if executor["activeTasks"] > 0]),
                    },
                    SPARK_EXECUTORS_METRICS,
                )
            except Exception:
                logger.exception("Could not gather spark executors metrics")

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
        elif self._cluster_mode == SPARK_STANDALONE_MODE:
            return self._standalone_init()
        elif self._cluster_mode == SPARK_MESOS_MODE:
            return self._mesos_init()
        else:
            raise ValueError(f"Invalid cluster mode {self._cluster_mode!r}")

    def _standalone_init(self) -> Dict[str, Tuple[str, str]]:
        """
        Return a dictionary of {app_id: (app_name, tracking_url)} for the running Spark applications
        """
        metrics_json = self._rest_request_to_json(
            self._master_address, SPARK_MASTER_STATE_PATH
        )
        running_apps = {}

        if metrics_json.get("activeapps") is not None:
            for app in metrics_json["activeapps"]:
                try:
                    app_id = app["id"]
                    app_name = app["name"]

                    # Parse through the HTML to grab the application driver's link
                    app_url = self._get_standalone_app_url(app_id)
                    logger.debug("Retrieved standalone app URL", app_url=app_url)

                    if app_id and app_name and app_url:
                        running_apps[app_id] = (app_name, app_url)
                        logger.debug("Added app to running apps", app_id=app_id, app_name=app_name, app_url=app_url)
                except KeyError:
                    logger.exception("Key error was found while iterating applications.")
                except Exception:
                    # it's possible for the requests to fail if the job
                    # completed since we got the list of apps.  Just continue
                    pass

        return running_apps

    def _get_standalone_app_url(self, app_id: str) -> Any:
        """
        Return the application URL from the app info page on the Spark master.
        Due to a bug, we need to parse the HTML manually because we cannot
        fetch JSON data from HTTP interface.
        """
        app_page = self._rest_request(
            self._master_address, SPARK_MASTER_APP_PATH, appId=app_id
        )
        dom = BeautifulSoup(app_page.text, "html.parser")

        app_detail_ui_links = dom.find_all("a", string="Application Detail UI")

        if app_detail_ui_links and len(app_detail_ui_links) == 1:
            logger.debug("There are running apps...")
            return app_detail_ui_links[0].attrs["href"]

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
