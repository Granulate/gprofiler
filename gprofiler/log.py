#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
import datetime
import json
import logging
import logging.handlers
import os
import re
import sys
import time
from logging import Logger, LogRecord
from typing import TYPE_CHECKING, Any, Dict, List, MutableMapping, Optional, Tuple

from requests import RequestException

from gprofiler import __version__
from gprofiler.exceptions import APIError, UninitializedStateException
from gprofiler.state import State, get_state

if TYPE_CHECKING:
    from gprofiler.client import APIClient

NO_SERVER_LOG_KEY = "no_server_log"
NO_SERVER_EXTRA_KEY = "no_extra_to_server"
RUN_ID_KEY = "run_id"
CYCLE_ID_KEY = "cycle_id"
GPROFILER_VERSION_KEY = "gprofiler_version"
LOGGER_NAME_RE = re.compile(r"gprofiler(?:\..+)?")


def get_logger_adapter(logger_name: str) -> logging.LoggerAdapter:
    # Validate the name starts with gprofiler (the root logger name), so logging parent logger propagation will work.
    assert LOGGER_NAME_RE.match(logger_name) is not None, "logger name must start with 'gprofiler'"
    logger = logging.getLogger(logger_name)
    return GProfilerLoggingAdapter(logger)


class GProfilerLoggingAdapter(logging.LoggerAdapter):
    LOGGING_KWARGS = ["exc_info", "extra", "stack_info"]

    def __init__(self, logger: Logger) -> None:
        super().__init__(logger, {})
        # Lazy initialization of state because it will be initialized only after calling `init_state` while the adapter
        # may initialize before (at module import stage)
        self._state: Optional[State] = None

    def _get_state_extra(self) -> Dict[str, str]:
        if self._state is None:
            try:
                self._state = get_state()
            except UninitializedStateException:
                return {}

        state_extra = {
            RUN_ID_KEY: self._state.run_id,
        }

        if self._state.cycle_id:
            state_extra[CYCLE_ID_KEY] = self._state.cycle_id

        return state_extra

    def process(self, msg: Any, kwargs: MutableMapping[str, Any]) -> Tuple[Any, MutableMapping[str, Any]]:
        extra_kwargs = {}
        logging_kwargs = {}
        for k, v in kwargs.items():
            if k in self.LOGGING_KWARGS:
                logging_kwargs[k] = v
            else:
                extra_kwargs[k] = v

        extra_kwargs.update(self._get_state_extra())
        extra_kwargs.update({GPROFILER_VERSION_KEY: __version__})

        extra = logging_kwargs.get("extra", {})
        extra["gprofiler_adapter_extra"] = extra_kwargs
        logging_kwargs["extra"] = extra
        return msg, logging_kwargs


class RemoteLogsHandler(logging.Handler):
    """
    logging.Handler that supports accumulating logs and sending them to server upon request.
    Because we don't want to lose log records before the APIClient initialized we support lazy initialization of
    APIClient, while logs are still accumulated from the beginning.
    """

    MAX_BUFFERED_RECORDS = 100 * 1000  # max number of records to buffer locally

    def __init__(self, path: str = "logs", api_client: Optional["APIClient"] = None) -> None:
        super().__init__(logging.DEBUG)
        self._api_client = api_client
        self._path = path
        self._logs: List[Dict] = []
        self._logger = get_logger_adapter("gprofiler.RemoteLogsHandler")
        self._truncated = False

        # The formatter is needed to format tracebacks
        self.setFormatter(logging.Formatter())

    def init_api_client(self, api_client: "APIClient") -> None:
        self._api_client = api_client

    def emit(self, record: LogRecord) -> None:
        extra_kwargs = record.gprofiler_adapter_extra  # type: ignore
        if extra_kwargs.pop(NO_SERVER_LOG_KEY, False):
            return

        self._logs.append(self._make_dict_record(record))
        # truncate logs to last N entries
        if len(self._logs) > self.MAX_BUFFERED_RECORDS:
            self._truncated = True
            self._logs[: -self.MAX_BUFFERED_RECORDS] = []

    def _make_dict_record(self, record: LogRecord) -> Dict[str, Any]:
        formatted_timestamp = datetime.datetime.utcfromtimestamp(record.created).isoformat()
        extra = record.gprofiler_adapter_extra  # type: ignore

        run_id = extra.pop(RUN_ID_KEY, None)
        if run_id is None:
            self._logger.error("state.run_id is not defined! probably a bug!")
            run_id = ""

        cycle_id = extra.pop(CYCLE_ID_KEY, "")

        # We don't want to serialize a JSON inside JSON but either don't want to fail record because of extra
        # serialization, so we test if the extra can be serialized and have a fail-safe.
        try:
            json.dumps(extra)
        except TypeError:
            self._logger.exception(
                f"Can't serialize extra (extra={extra!r}), sending empty extra", bad_extra=repr(extra)
            )
            extra = {}

        assert self.formatter is not None
        if record.exc_info:
            # Use cached exc_text if available.
            exception_traceback = (
                record.exc_text if record.exc_text else self.formatter.formatException(record.exc_info)
            )
        else:
            exception_traceback = ""

        extra = extra if not extra.pop(NO_SERVER_EXTRA_KEY, False) else {}

        return {
            "message": record.message,
            "level": record.levelname,
            "timestamp": formatted_timestamp,
            "extra": extra,
            "logger_name": record.name,
            "exception": exception_traceback,
            RUN_ID_KEY: run_id,
            CYCLE_ID_KEY: cycle_id,
        }

    def try_send_log_to_server(self) -> None:
        assert self._api_client is not None, "APIClient is not initialized, can't send logs to server"
        # Snapshot the current num logs because logs list might be extended meanwhile.
        logs_count = len(self._logs)
        try:
            if self._truncated:
                self._logger.warning(
                    f"Log buffer truncation has occurred as the maximum number of records ({self.MAX_BUFFERED_RECORDS})"
                    " was reached",
                )
                self._truncated = False
            self._api_client.post(self._path, data=self._logs[:logs_count], api_version="v1")
        except (APIError, RequestException):
            self._logger.exception("Failed sending logs to server")
        else:
            # If succeeded, remove the sent logs from the list.
            self._logs[:logs_count] = []


class _ExtraFormatter(logging.Formatter):
    FILTERED_EXTRA_KEYS = [CYCLE_ID_KEY, RUN_ID_KEY, NO_SERVER_LOG_KEY, NO_SERVER_EXTRA_KEY, GPROFILER_VERSION_KEY]

    def format(self, record: LogRecord) -> str:
        formatted = super().format(record)
        extra = record.gprofiler_adapter_extra  # type: ignore

        formatted_extra = ", ".join(f"{k}={v}" for k, v in extra.items() if k not in self.FILTERED_EXTRA_KEYS)
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
