#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import logging
import logging.handlers
import os
import re
import sys
import time
from logging import LogRecord
from typing import Any, Dict, Mapping, Optional
from urllib.parse import urlparse

from glogger.extra_adapter import ExtraAdapter
from glogger.handler import BatchRequestsHandler
from glogger.sender import Sender

from gprofiler import __version__
from gprofiler.state import get_state

NO_SERVER_LOG_KEY = "no_server_log"
NO_SERVER_EXTRA_KEY = "no_extra_to_server"
CYCLE_ID_KEY = "cycle_id"
LOGGER_NAME_RE = re.compile(r"gprofiler(?:\..+)?")


def get_logger_adapter(logger_name: str) -> logging.LoggerAdapter:
    # Validate the name starts with gprofiler (the root logger name), so logging parent logger propagation will work.
    assert LOGGER_NAME_RE.match(logger_name) is not None, "logger name must start with 'gprofiler'"
    return GProfilerExtraAdapter(logging.getLogger(logger_name))


class GProfilerExtraAdapter(ExtraAdapter):
    def get_extra(self, **kwargs: Mapping[str, Any]) -> Mapping[str, Any]:
        extra = super().get_extra(**kwargs)
        # here we add fields which change during the lifetime of gProfiler.
        # fields that do not change go in RemoteLogsHandler.get_metadata().
        assert CYCLE_ID_KEY not in extra
        return {**extra, CYCLE_ID_KEY: get_state().cycle_id}


class RemoteLogsHandler(BatchRequestsHandler):
    """
    logging.Handler that supports accumulating logs and sending them to server upon request.
    Because we don't want to lose log records before the APIClient initialized we support lazy initialization of
    APIClient, while logs are still accumulated from the beginning.
    """

    MAX_BUFFERED_RECORDS = 100 * 1000  # max number of records to buffer locally

    def __init__(self, server_address: str, auth_token: str, service_name: str, verify: bool) -> None:
        self._service_name = service_name
        url = urlparse(server_address)
        super().__init__(
            Sender(
                application_name="gprofiler",
                auth_token=auth_token,
                scheme=url.scheme,
                server_address=url.netloc,
                verify=verify,
            )
        )

    def emit(self, record: LogRecord) -> None:
        extra = self.get_extra_fields(record)

        if extra.pop(NO_SERVER_LOG_KEY, False):
            return

        if extra.pop(NO_SERVER_EXTRA_KEY, False):
            record.extra = {}

        return super().emit(record)

    def get_metadata(self) -> Dict[str, str]:
        from gprofiler.metadata.system_metadata import get_hostname_or_none

        state = get_state()
        hostname = get_hostname_or_none()

        # here we add fields which don't change during the lifetime of gProfiler.
        # fields that do change go in GProfilerExtraAdapter.get_extra().
        metadata = {
            "run_id": state.run_id,
            "gprofiler_version": __version__,
            "service_name": self._service_name,
        }
        if hostname is not None:
            metadata["hostname"] = hostname

        return metadata

    def update_service_name(self, service_name: str) -> None:
        """
        Used to update the service name in services where it can change after gProfiler starts (for example, if the
        service name is derived from the environment post inittialization).
        The next batch sent will have the new service name.
        """
        self._service_name = service_name


class _ExtraFormatter(logging.Formatter):
    FILTERED_EXTRA_KEYS = [NO_SERVER_LOG_KEY, NO_SERVER_EXTRA_KEY, CYCLE_ID_KEY]  # don't print those fields locally

    def format(self, record: LogRecord) -> str:
        formatted = super().format(record)

        formatted_extra = ", ".join(
            f"{k}={v}" for k, v in record.__dict__.get("extra", {}).items() if k not in self.FILTERED_EXTRA_KEYS
        )
        if formatted_extra:
            formatted = f"{formatted} ({formatted_extra})"

        return formatted


class _UTCFormatter(logging.Formatter):
    # Patch formatTime to be GMT (UTC) for all formatters,
    # see https://docs.python.org/3/library/logging.html?highlight=formattime#logging.Formatter.formatTime
    converter = time.gmtime


class GProfilerFormatter(_ExtraFormatter, _UTCFormatter):
    pass


def initial_root_logger_setup(
    stream_level: int,
    log_file_path: str,
    rotate_max_bytes: int,
    rotate_backup_count: int,
    remote_logs_handler: Optional[RemoteLogsHandler],
) -> logging.LoggerAdapter:
    logger_adapter = get_logger_adapter("gprofiler")
    logger_adapter.setLevel(logging.DEBUG)

    stream_handler = logging.StreamHandler(stream=sys.stdout)
    stream_handler.setLevel(stream_level)
    if stream_level < logging.INFO:
        stream_handler.setFormatter(GProfilerFormatter("[%(asctime)s] %(levelname)s: %(name)s: %(message)s"))
    else:
        stream_handler.setFormatter(GProfilerFormatter("[%(asctime)s] %(message)s", "%H:%M:%S"))
    logger_adapter.logger.addHandler(stream_handler)

    os.makedirs(os.path.dirname(log_file_path), exist_ok=True)
    file_handler = logging.handlers.RotatingFileHandler(
        log_file_path,
        maxBytes=rotate_max_bytes,
        backupCount=rotate_backup_count,
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(GProfilerFormatter("[%(asctime)s] %(levelname)s: %(name)s: %(message)s"))
    logger_adapter.logger.addHandler(file_handler)

    if remote_logs_handler is not None:
        logger_adapter.logger.addHandler(remote_logs_handler)

    return logger_adapter
