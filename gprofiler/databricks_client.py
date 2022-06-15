#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import json
import os
import time
from typing import Optional

import requests

from gprofiler.log import get_logger_adapter

HOST_KEY_NAME = "*.sink.ganglia.host"
DATABRICKS_DEPLOY_CONF_PATH = "/databricks/common/conf/deploy.conf"
DATABRICKS_METRICS_PROP_PATH = "/databricks/spark/conf/metrics.properties"
CLUSTER_TAGS_KEY = "spark.databricks.clusterUsageTags.clusterAllTags"
JOB_NAME_KEY = "RunName"
SPARKUI_APPS_URL = "http://{}/api/v1/applications"
REQUEST_TIMEOUT = 5
DEFAULT_WEBUI_PORT = 40001
MAX_RETRIES = 4

logger = get_logger_adapter(__name__)


class DatabricksClient:
    def __init__(self) -> None:
        try:
            self.job_name = self.get_job_name()
        except Exception as ex:
            self.job_name = None
            logger.warning(
                f"Failed initializing Databricks client. Databricks job name will not be included in "
                f"ephemeral clusters. Error: {ex}"
            )

    @staticmethod
    def get_webui_address() -> Optional[str]:
        with open(DATABRICKS_METRICS_PROP_PATH) as f:
            properties = f.read()
        host = dict([line.split("=") for line in properties.splitlines()])[HOST_KEY_NAME]
        return f"{host}:{DEFAULT_WEBUI_PORT}"

    def get_job_name(self) -> Optional[str]:
        # Retry in case of a connection error, as the metrics server might not be up yet.
        for i in range(MAX_RETRIES):
            time.sleep(30)
            try:
                return self._get_job_name_impl()
            except requests.exceptions.ConnectionError as ex:
                if i == MAX_RETRIES - 1:
                    raise ex
        return None

    def _get_job_name_impl(self) -> Optional[str]:
        # Make sure we're running on a databricks machine
        if not os.path.isfile(DATABRICKS_DEPLOY_CONF_PATH):
            return None
        webui = self.get_webui_address()
        # The API used: https://spark.apache.org/docs/latest/monitoring.html#rest-api
        apps_url = SPARKUI_APPS_URL.format(webui)
        resp = requests.get(apps_url, timeout=REQUEST_TIMEOUT)
        if not resp.ok:
            logger.warning(
                f"Failed initializing Databricks client. {apps_url!r} request failed, status_code: {resp.status_code}."
            )
            return None
        apps = resp.json()
        if len(apps) == 0:
            logger.warning("Failed initializing Databricks client. There are no apps.")
            return None
        # There's an assumption that only one app exists, and even if there are more -
        # the name of the job should be the same.
        env_url = f"{apps_url}/{apps[0]['id']}/environment"
        resp = requests.get(env_url, timeout=REQUEST_TIMEOUT)
        if not resp.ok:
            logger.warning(
                f"Failed initializing Databricks client. {env_url!r} request failed, status_code: {resp.status_code}."
            )
            return None
        env = resp.json()
        props = env["sparkProperties"]
        for prop in props:
            if prop[0] == CLUSTER_TAGS_KEY:
                for tag in json.loads(prop[1]):
                    if tag["key"] == JOB_NAME_KEY:
                        return str(tag["value"])
        return None
