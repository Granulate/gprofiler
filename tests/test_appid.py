#
# Copyright (c) Granulate. All rights reserved.
# Licensed under the AGPL3 License. See LICENSE.md in the project root for license information.
#
from typing import List
from unittest.mock import Mock

from gprofiler.metadata.application_identifiers import get_application_name

PROCESS_CWD = "/my/dir"


def process_with_cmdline(cmdline: List[str]) -> Mock:
    process = Mock()
    process.cmdline.return_value = cmdline
    process.cwd.return_value = PROCESS_CWD
    return process


def test_gunicorn_title():
    assert f"gunicorn: {PROCESS_CWD}/my/wsgi.py" == get_application_name(
        process_with_cmdline(["gunicorn: master [my.wsgi:app]"])
    )
    assert f"gunicorn: {PROCESS_CWD}/my/wsgi.py" == get_application_name(
        process_with_cmdline(["gunicorn: worker [my.wsgi:app]"])
    )


def test_gunicorn():
    assert f"gunicorn: {PROCESS_CWD}/my/wsgi.py" == get_application_name(
        process_with_cmdline(["gunicorn", "a", "b", "my.wsgi:app"])
    )
    assert f"gunicorn: {PROCESS_CWD}/my/wsgi.py" == get_application_name(
        process_with_cmdline(["python", "/path/to/gunicorn", "a", "b", "my.wsgi:app"])
    )
    assert "gunicorn: /path/to/my/wsgi.py" == get_application_name(
        process_with_cmdline(["python", "/path/to/gunicorn", "a", "b", "/path/to/my/wsgi:app"])
    )


def test_celery():
    # celery -A
    assert f"celery: {PROCESS_CWD}/app1.py" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "-A", "app1"])
    )
    assert "celery: /path/to/app1.py" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "-A", "/path/to/app1"])
    )
    # python celery -A
    assert f"celery: {PROCESS_CWD}/app1.py" == get_application_name(
        process_with_cmdline(["python", "/path/to/celery", "a", "b", "-A", "app1"])
    )
    assert "celery: /path/to/app1.py" == get_application_name(
        process_with_cmdline(["python", "/path/to/celery", "a", "b", "-A", "/path/to/app1"])
    )
    # --app app
    assert f"celery: {PROCESS_CWD}/app2.py" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "--app", "app2"])
    )
    assert "celery: /path/to/app2.py" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "--app", "/path/to/app2"])
    )
    # --app=app
    assert f"celery: {PROCESS_CWD}/app3.py" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "--app=app3"])
    )
    assert "celery: /path/to/app3.py" == get_application_name(
        process_with_cmdline(["celery", "a", "b", "--app=/path/to/app3"])
    )


def test_pyspark():
    assert "pyspark" == get_application_name(process_with_cmdline(["python", "-m", "pyspark.daemon"]))


def test_python():
    # python -m & different python bins
    assert "python: -m myapp" == get_application_name(process_with_cmdline(["python", "-m", "myapp"]))
    assert "python: -m myapp" == get_application_name(process_with_cmdline(["python3", "-m", "myapp"]))
    assert "python: -m myapp" == get_application_name(process_with_cmdline(["python3.8", "-m", "myapp"]))
    assert "python: -m myapp" == get_application_name(process_with_cmdline(["python2", "-m", "myapp"]))
    assert "python: -m myapp" == get_application_name(process_with_cmdline(["python2.7", "-m", "myapp"]))
    # python mod.py
    assert "python: /path/to/mod.py" == get_application_name(process_with_cmdline(["python2.7", "/path/to/mod.py"]))
    assert f"python: {PROCESS_CWD}/mod.py" == get_application_name(process_with_cmdline(["python2.7", "mod.py"]))
