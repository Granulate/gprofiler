#
# Copyright (C) 2026 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

import logging
import os
from collections import Counter
from contextlib import contextmanager
from threading import Thread
from typing import Iterable, Iterator, TextIO, Tuple

import pytest
from pytest import LogCaptureFixture

from gprofiler.utils.iaprof import STALL_REASONS, IaprofOutputReader, IaprofParser


def _parse_lines(parser: IaprofParser, lines: Iterable[str]) -> None:
    for line in lines:
        parser.parse_line(line)


@contextmanager
def _reader_pipe() -> Iterator[Tuple[IaprofOutputReader, TextIO]]:
    read_fd, write_fd = os.pipe()
    read_stream = os.fdopen(read_fd)
    output = os.fdopen(write_fd, "w")
    reader = IaprofOutputReader()
    thread = Thread(target=reader.read, args=(read_stream,))
    thread.start()
    try:
        yield reader, output
    finally:
        output.close()
        thread.join(timeout=5)
        read_stream.close()
        assert not thread.is_alive()


def _write_lines(output: TextIO, *lines: str) -> None:
    output.write("\n".join(lines) + "\n")
    output.flush()


def test_iaprof_eustall_stacks() -> None:
    parser = IaprofParser()
    _parse_lines(
        parser,
        [
            "string\t1\tapp",
            "string\t2\t_start;main",
            "string\t3\tsource;cpp",
            "string\t4\tkernel",
            "string\t5\tmov",
            "interval\t0\t1.0",
            "kernel\t0x1000\t1\t42\t2\t3\t4",
            "eustall\t0x10\t5\t1\t2\t3\t4\t5\t6\t7\t8\t9\t10",
        ],
    )

    prefix = "app;_start;main;-;source|cpp_[G];kernel_[G];mov_[g]"
    assert parser.take_snapshot() == {
        42: Counter({f"{prefix};{reason}_[g];0x10_[g]": count for reason, count in zip(STALL_REASONS, range(1, 11))})
    }


def test_iaprof_snapshot_resets_counters_and_retains_strings() -> None:
    parser = IaprofParser()
    _parse_lines(
        parser,
        [
            "string\t1\tapp",
            "string\t2\tkernel",
            "string\t3\tmov",
            "kernel\t0x1000\t1\t42\t0\t0\t2",
            "eustall\t0x10\t3\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0",
        ],
    )
    assert parser.parse_line("interval\t1\t2.0") == 1

    stack = "app;-;[unknown]_[G];kernel_[G];mov_[g];active_[g];0x10_[g]"
    assert parser.take_snapshot() == {42: Counter({stack: 2})}
    assert parser.take_snapshot() == {}

    _parse_lines(
        parser,
        [
            "kernel\t0x1000\t1\t42\t0\t0\t2",
            "eustall\t0x10\t3\t3\t0\t0\t0\t0\t0\t0\t0\t0\t0",
        ],
    )
    assert parser.take_snapshot() == {42: Counter({stack: 3})}


def test_iaprof_interval_clears_kernel() -> None:
    parser = IaprofParser()
    _parse_lines(parser, ["string\t1\tapp", "kernel\t0x1000\t1\t42\t0\t0\t0"])

    assert parser.parse_line("interval\t1\t2.0") == 1
    assert parser.parse_line("eustall\t0x10\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0") is None
    assert parser.take_snapshot() == {}


def test_iaprof_bad_records_are_skipped(caplog: LogCaptureFixture) -> None:
    parser = IaprofParser()
    caplog.set_level(logging.WARNING)
    _parse_lines(
        parser,
        [
            "kernel\t0x1000\t0\t0\t0\t0\t0",
            "eustall\t0x10\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0",
            "invalid",
        ],
    )

    assert parser.take_snapshot() == {}
    assert "Skipped 3 malformed iaprof records" in caplog.text


def test_iaprof_output_reader_snapshots_at_interval() -> None:
    with _reader_pipe() as (reader, output):
        snapshot = reader.request_snapshot()
        _write_lines(
            output,
            "string\t1\tapp",
            "string\t2\tkernel",
            "string\t3\tmov",
            "kernel\t0x1000\t1\t42\t0\t0\t2",
            "eustall\t0x10\t3\t2\t0\t0\t0\t0\t0\t0\t0\t0\t0",
            "interval\t1\t2.0",
        )

        stack = "app;-;[unknown]_[G];kernel_[G];mov_[g];active_[g];0x10_[g]"
        assert snapshot.result(timeout=5) == {42: Counter({stack: 2})}

        snapshot = reader.request_snapshot()
        _write_lines(
            output,
            "kernel\t0x1000\t1\t42\t0\t0\t2",
            "eustall\t0x10\t3\t3\t0\t0\t0\t0\t0\t0\t0\t0\t0",
            "interval\t2\t3.0",
        )
        assert snapshot.result(timeout=5) == {42: Counter({stack: 3})}


def test_iaprof_output_reader_empty_snapshot() -> None:
    with _reader_pipe() as (reader, output):
        snapshot = reader.request_snapshot()
        _write_lines(output, "interval\t1\t2.0")
        assert snapshot.result(timeout=5) == {}


def test_iaprof_output_reader_wakes_snapshot_on_eof() -> None:
    with _reader_pipe() as (reader, output):
        snapshot = reader.request_snapshot()
        output.close()
        with pytest.raises(EOFError, match="iaprof output closed"):
            snapshot.result(timeout=5)
