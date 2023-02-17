#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import os
import re
from typing import Optional

from psutil import Process

from gprofiler.exceptions import CalledProcessError
from gprofiler.log import get_logger_adapter
from gprofiler.metadata.base_application_identifier import _ApplicationIdentifier
from gprofiler.profilers.java import jattach_path
from gprofiler.utils import run_process

_logger = get_logger_adapter(__name__)


class _JavaJarApplicationIdentifier(_ApplicationIdentifier):
    def get_app_id(self, process: Process) -> Optional[str]:
        try:
            java_properties = run_process([jattach_path(), str(process.pid), "jcmd", "VM.command_line"]).stdout.decode()
            java_command = None
            java_args = []
            for line in java_properties.splitlines():
                if line.startswith("jvm_args:"):
                    if (
                        self.enrichment_options is not None
                        and self.enrichment_options.application_identifier_args_filters
                    ):
                        for arg in line[line.find(":") + 1 :].strip().split(" "):
                            if any(
                                re.search(flag_filter, arg)
                                for flag_filter in self.enrichment_options.application_identifier_args_filters
                            ):
                                java_args.append(arg)
                if line.startswith("java_command:"):
                    java_command = line[line.find(":") + 1 :].strip().split(" ", 1)[0]
            if java_command:
                return f"java: {java_command}{' (' + ' '.join(java_args) + ')' if java_args else ''}"
        except CalledProcessError as e:
            _logger.warning(f"Couldn't get Java properties for process {process.pid}: {e.stderr}")

        return None


class _JavaSparkApplicationIdentifier(_ApplicationIdentifier):
    _JAVA_SPARK_EXECUTOR_ARG = "org.apache.spark.executor"
    _SPARK_PROPS_FILE = os.path.join("__spark_conf__", "__spark_conf__.properties")
    _APP_NAME_NOT_FOUND = "app name not found"
    _APP_NAME_KEY = "spark.app.name"
    _APP_ID_KEY = "--app-id"

    @staticmethod
    def _is_java_spark_executor(process: Process) -> bool:
        args = process.cmdline()
        return any(_JavaSparkApplicationIdentifier._JAVA_SPARK_EXECUTOR_ARG in arg for arg in args)

    def get_app_id(self, process: Process) -> Optional[str]:
        if not self._is_java_spark_executor(process):
            return None
        props_path = os.path.join(process.cwd(), self._SPARK_PROPS_FILE)
        if not os.path.exists(props_path):
            _logger.warning(
                f"Spark props file doesn't exist: {props_path}. \
                        Process args: {process.cmdline()}, pid: {process.pid}"
            )
            return self._APP_NAME_NOT_FOUND
        with open(props_path) as f:
            props_text = f.read()
        props = dict(
            [line.split("#", 1)[0].split("=", 1) for line in props_text.splitlines() if not line.startswith("#")]
        )
        if self._APP_NAME_KEY in props:
            return f"spark: {props[self._APP_NAME_KEY]}"
        args = process.cmdline()
        try:
            for idx, x in enumerate(args):
                if x == self._APP_ID_KEY:
                    return f"spark: {args[idx+1]}"
        except Exception:
            pass
        return self._APP_NAME_NOT_FOUND
