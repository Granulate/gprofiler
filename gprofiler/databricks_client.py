#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#

import json
import os.path
from typing import Dict, Optional

import requests

from gprofiler.log import get_logger_adapter

HOST_KEY_NAME = "sink.ganglia.host"
DATABRICKS_DEPLOY_CONF_PATH = "/databricks/common/conf/deploy.conf"
DATABRICKS_METRICS_PROP_PATH = "/databricks/spark/conf/metrics.properties"
CLUSTER_TAGS_KEY = "spark.databricks.clusterUsageTags.clusterAllTags"
JOB_NAME_KEY = "RunName"
REQUEST_TIMEOUT = 5
DEFAULT_WEBUI_PORT = 40001

logger = get_logger_adapter(__name__)


class DatabricksClient:
    def __init__(self):
        self._cluster_deploy_conf_tags: Optional[Dict[str, str]] = None
        self._is_databricks: bool = False
        self._is_job: bool = False
        try:
            self.job_name = self.get_job_name()
        except Exception:
            self.job_name = None
            logger.warning(
                "Failed initiating Databricks client. Databricks job name will not be included in "
                "ephemeral clusters."
            )

    @property
    def is_databricks_job(self) -> bool:
        return self._is_databricks and self._is_job

    def get_webui_address(self) -> Optional[str]:
        with open(DATABRICKS_METRICS_PROP_PATH) as metrics_properties_file:
            metrics_properties_text = metrics_properties_file.read()
        host_start_index = metrics_properties_text.find(HOST_KEY_NAME) + len(HOST_KEY_NAME) + 1
        if host_start_index == -1:
            return None
        host_end_index = metrics_properties_text.find("\n", host_start_index)
        if host_end_index == -1:
            host = metrics_properties_text[host_start_index:]
        else:
            host = metrics_properties_text[host_start_index:host_end_index]

        return f"{host}:{DEFAULT_WEBUI_PORT}"

    def get_job_name(self) -> Optional[str]:
        webui = self.get_webui_address()
        applications_response = requests.get(f"http://{webui}/api/v1/applications", headers={}, timeout=REQUEST_TIMEOUT)
        if not applications_response.ok:
            logger.warning("Failed initiating Databricks client. `http://{webui}/api/v1/applications` request failed.")
            return None
        apps = applications_response.json()
        if len(apps) == 0:
            logger.warning("Failed initiating Databricks client. There are no apps.")
            return None
        env_response = requests.get(
            f"http://{webui}/api/v1/applications/{apps[0]['id']}/environment", headers={}, timeout=REQUEST_TIMEOUT
        )
        if not env_response.ok:
            logger.warning(
                f"Failed initiating Databricks client. `http://{webui}/api/v1/applications/{apps[0]['id']}"
                + "/environment` request failed."
            )
            return None
        env = env_response.json()
        props = env["sparkProperties"]
        for prop in props:
            if prop[0] == CLUSTER_TAGS_KEY:
                for tag in json.loads(prop[1]):
                    if tag["key"] == JOB_NAME_KEY:
                        self._is_job = True
                        return tag["value"]
