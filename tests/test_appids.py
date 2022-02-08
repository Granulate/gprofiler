#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from io import StringIO
from typing import List, TextIO
from unittest.mock import Mock

from psutil import Process
from pytest import MonkeyPatch

from gprofiler.metadata.application_identifiers import _UwsgiApplicationIdentifier, get_application_name

PROCESS_CWD = "/my/dir"


def process_with_cmdline(cmdline: List[str]) -> Mock:
    process = Mock()
    process.cmdline.return_value = cmdline
    process.cwd.return_value = PROCESS_CWD
    return process


def test_gunicorn_title() -> None:
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_application_name(
        process_with_cmdline(["gunicorn: master [my.wsgi:app]"])
    )
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_application_name(
        process_with_cmdline(["gunicorn: worker [my.wsgi:app]"])
    )


def test_gunicorn() -> None:
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_application_name(
        process_with_cmdline(["gunicorn", "a", "b", "my.wsgi:app"])
    )
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_application_name(
        process_with_cmdline(["python", "/path/to/gunicorn", "a", "b", "my.wsgi:app"])
    )
    assert "gunicorn: /path/to/my/wsgi:app (/path/to/my/wsgi.py)" == get_application_name(
        process_with_cmdline(["python", "/path/to/gunicorn", "a", "b", "/path/to/my/wsgi:app"])
    )


def test_uwsgi_wsgi_file() -> None:
    assert f"uwsgi: my.wsgi ({PROCESS_CWD}/my/wsgi.py)" == get_application_name(
        process_with_cmdline(["uwsgi", "a", "b", "-w", "my.wsgi"])
    )
    assert f"uwsgi: my.wsgi ({PROCESS_CWD}/my/wsgi.py)" == get_application_name(
        process_with_cmdline(["uwsgi", "a", "b", "--wsgi-file", "my.wsgi"])
    )
    assert f"uwsgi: my.wsgi ({PROCESS_CWD}/my/wsgi.py)" == get_application_name(
        process_with_cmdline(["uwsgi", "a", "b", "--wsgi-file=my.wsgi"])
    )


def test_uwsgi_ini_file(monkeypatch: MonkeyPatch) -> None:
    config = "[app:blabla]\nxx = yy\n\n[uwsgi]\nmodule = mymod"

    def get_uwsgi_config(process: Process, config_file: str) -> TextIO:
        return StringIO(config)

    monkeypatch.setattr(_UwsgiApplicationIdentifier, "_open_uwsgi_config_file", get_uwsgi_config)

    # --ini
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_application_name(
        process_with_cmdline(["uwsgi", "a", "b", "--ini", "my.ini"])
    )
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_application_name(
        process_with_cmdline(["uwsgi", "a", "b", "--ini=my.ini"])
    )
    # --ini-paste
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_application_name(
        process_with_cmdline(["uwsgi", "a", "b", "--ini-paste", "my.ini"])
    )
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_application_name(
        process_with_cmdline(["uwsgi", "a", "b", "--ini-paste=my.ini"])
    )


def test_celery() -> None:
    # celery -A
    assert f"celery: app1 ({PROCESS_CWD}/app1.py)" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "-A", "app1"])
    )
    assert "celery: /path/to/app1 (/path/to/app1.py)" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "-A", "/path/to/app1"])
    )
    # python celery -A
    assert f"celery: app1 ({PROCESS_CWD}/app1.py)" == get_application_name(
        process_with_cmdline(["python", "/path/to/celery", "a", "b", "-A", "app1"])
    )
    assert "celery: /path/to/app1 (/path/to/app1.py)" == get_application_name(
        process_with_cmdline(["python", "/path/to/celery", "a", "b", "-A", "/path/to/app1"])
    )
    # --app app
    assert f"celery: app2 ({PROCESS_CWD}/app2.py)" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "--app", "app2"])
    )
    assert "celery: /path/to/app2 (/path/to/app2.py)" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "--app", "/path/to/app2"])
    )
    # --app=app
    assert f"celery: app3 ({PROCESS_CWD}/app3.py)" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "--app=app3"])
    )
    assert "celery: /path/to/app3 (/path/to/app3.py)" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "--app=/path/to/app3"])
    )


def test_pyspark() -> None:
    assert "pyspark" == get_application_name(process_with_cmdline(["python", "-m", "pyspark.daemon"]))


def test_python() -> None:
    # python -m & different python bins
    assert "python: -m myapp" == get_application_name(process_with_cmdline(["python", "-m", "myapp"]))
    assert "python: -m myapp" == get_application_name(process_with_cmdline(["python3", "-m", "myapp"]))
    assert "python: -m myapp" == get_application_name(process_with_cmdline(["python3.8", "-m", "myapp"]))
    assert "python: -m myapp" == get_application_name(process_with_cmdline(["python2", "-m", "myapp"]))
    assert "python: -m myapp.x.y" == get_application_name(process_with_cmdline(["python2.7", "-m", "myapp.x.y"]))
    # python mod.py
    assert "python: /path/to/mod.py (/path/to/mod.py)" == get_application_name(
        process_with_cmdline(["python2.7", "/path/to/mod.py"])
    )
    assert f"python: mod.py ({PROCESS_CWD}/mod.py)" == get_application_name(
        process_with_cmdline(["python2.7", "mod.py"])
    )
