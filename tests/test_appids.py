#
# Copyright (C) 2023 Intel Corporation
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
from io import StringIO
from typing import List, TextIO
from unittest.mock import Mock

from psutil import Process
from pytest import MonkeyPatch

from gprofiler.metadata.application_identifiers import (
    _UwsgiApplicationIdentifier,
    get_node_app_id,
    get_python_app_id,
    get_ruby_app_id,
)

PROCESS_CWD = "/my/dir"


def process_with_cmdline(cmdline: List[str]) -> Mock:
    process = Mock()
    process.cmdline.return_value = cmdline
    process.cwd.return_value = PROCESS_CWD
    return process


def test_gunicorn_title() -> None:
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["gunicorn: master [my.wsgi:app]"])
    )
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["gunicorn: worker [my.wsgi:app]"])
    )


def test_gunicorn() -> None:
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["gunicorn", "a", "b", "my.wsgi:app"])
    )
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["python", "/path/to/gunicorn", "a", "b", "my.wsgi:app"])
    )
    assert "gunicorn: /path/to/my/wsgi:app (/path/to/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["python", "/path/to/gunicorn", "a", "b", "/path/to/my/wsgi:app"])
    )
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["gunicorn", "-a", "5", "-b", "0.0.0.0:80", "my.wsgi:app"])
    )
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["gunicorn", "-a", "5", "my.wsgi:app", "-b", "0.0.0.0:80"])
    )
    assert f"gunicorn: my.wsgi:app ({PROCESS_CWD}/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["gunicorn", "my.wsgi:app", "-a", "4", "-k", "uvicorn.workers.UvicornWorkerpython"])
    )
    assert "gunicorn: unknown app name (unknown app name)" == get_python_app_id(
        process_with_cmdline(["gunicorn", "-a", "5", "-b", "0.0.0.0:80"])
    )


def test_uvicorn() -> None:
    assert f"uvicorn: my.asgi:app ({PROCESS_CWD}/my/asgi.py)" == get_python_app_id(
        process_with_cmdline(["uvicorn", "a", "b", "my.asgi:app"])
    )
    assert f"uvicorn: my.asgi:app ({PROCESS_CWD}/my/asgi.py)" == get_python_app_id(
        process_with_cmdline(["python", "/path/to/uvicorn", "a", "b", "my.asgi:app"])
    )
    assert "uvicorn: /path/to/my/asgi:app (/path/to/my/asgi.py)" == get_python_app_id(
        process_with_cmdline(["python", "/path/to/uvicorn", "a", "b", "/path/to/my/asgi:app"])
    )
    assert f"uvicorn: my.asgi:app ({PROCESS_CWD}/my/asgi.py)" == get_python_app_id(
        process_with_cmdline(["uvicorn", "--workers", "5", "--host", "0.0.0.0", "my.asgi:app"])
    )
    assert f"uvicorn: my.asgi:app ({PROCESS_CWD}/my/asgi.py)" == get_python_app_id(
        process_with_cmdline(["uvicorn", "--workers", "5", "my.asgi:app", "--host", "0.0.0.0"])
    )
    assert f"uvicorn: my.asgi:app ({PROCESS_CWD}/my/asgi.py)" == get_python_app_id(
        process_with_cmdline(["uvicorn", "a", "--factory", "my.asgi:app"])
    )
    assert "uvicorn: unknown app name (unknown app name)" == get_python_app_id(
        process_with_cmdline(["uvicorn", "-a", "5", "-b", "0.0.0.0:80"])
    )


def test_uwsgi_wsgi_file() -> None:
    assert f"uwsgi: my.wsgi ({PROCESS_CWD}/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "-w", "my.wsgi"])
    )
    assert f"uwsgi: my.wsgi ({PROCESS_CWD}/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "--wsgi-file", "my.wsgi"])
    )
    assert f"uwsgi: my.wsgi ({PROCESS_CWD}/my/wsgi.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "--wsgi-file=my.wsgi"])
    )


def test_uwsgi_ini_file(monkeypatch: MonkeyPatch) -> None:
    config = "[app:blabla]\nxx = yy\n\n[uwsgi]\nmodule = mymod"

    def get_uwsgi_config(process: Process, config_file: str) -> TextIO:
        assert config_file == "my.ini"
        return StringIO(config)

    monkeypatch.setattr(_UwsgiApplicationIdentifier, "_open_uwsgi_config_file", get_uwsgi_config)

    # --ini
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "--ini", "my.ini"])
    )
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "--ini=my.ini"])
    )

    # --ini-paste
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "--ini-paste", "my.ini"])
    )
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "--ini-paste=my.ini"])
    )

    # --ini-paste-logged
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "--ini-paste-logged", "my.ini"])
    )
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "--ini-paste-logged=my.ini"])
    )

    # just one '.ini' file - selected
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "my.ini"])
    )
    # many '.ini' files - not selected
    assert "uwsgi: ini file / wsgi module not found" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "my.ini", "my2.ini"])
    )
    # many '.ini' files but one is with --ini - it is selected
    assert f"uwsgi: my.ini ({PROCESS_CWD}/mymod.py)" == get_python_app_id(
        process_with_cmdline(["uwsgi", "a", "b", "--ini=my.ini", "my2.ini"])
    )

    # --ini with no uwsgi section
    config = "[app:blabla]\nxx = yy\n\n"
    assert "uwsgi: my.ini" == get_python_app_id(process_with_cmdline(["uwsgi", "a", "b", "--ini", "my.ini"]))


def test_celery_with_app() -> None:
    # celery -A
    assert f"celery: app1 ({PROCESS_CWD}/app1.py)" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "-A", "app1"])
    )
    assert "celery: /path/to/app1 (/path/to/app1.py)" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "-A", "/path/to/app1"])
    )
    assert "celery: /path/to/app1 (/path/to/app1.py)" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "-A/path/to/app1"])
    )
    # python celery -A
    assert f"celery: app1 ({PROCESS_CWD}/app1.py)" == get_python_app_id(
        process_with_cmdline(["python", "/path/to/celery", "a", "b", "-A", "app1"])
    )
    assert "celery: /path/to/app1 (/path/to/app1.py)" == get_python_app_id(
        process_with_cmdline(["python", "/path/to/celery", "a", "b", "-A", "/path/to/app1"])
    )
    # --app app
    assert f"celery: app2 ({PROCESS_CWD}/app2.py)" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "--app", "app2"])
    )
    assert "celery: /path/to/app2 (/path/to/app2.py)" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "--app", "/path/to/app2"])
    )
    # --app=app
    assert f"celery: app3 ({PROCESS_CWD}/app3.py)" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "--app=app3"])
    )
    assert "celery: /path/to/app3 (/path/to/app3.py)" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "--app=/path/to/app3"])
    )


def test_celery_with_queue() -> None:
    # celery -Q queue
    assert f"celery queue: qqq ({PROCESS_CWD})" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "-Q", "qqq"])
    )
    # celery -Qqueue
    assert f"celery queue: qqq ({PROCESS_CWD})" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "-Qqqq"])
    )
    # python celery -Q queue
    assert f"celery queue: qqq ({PROCESS_CWD})" == get_python_app_id(
        process_with_cmdline(["python", "/path/to/celery", "a", "b", "-Q", "qqq"])
    )
    # --queues queue
    assert f"celery queue: qqq ({PROCESS_CWD})" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "--queues", "qqq"])
    )
    # --queues=queue
    assert f"celery queue: qqq ({PROCESS_CWD})" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "--queues=qqq"])
    )
    # multiple queues
    assert f"celery queue: qqq,www ({PROCESS_CWD})" == get_python_app_id(
        process_with_cmdline(["celery", "a", "b", "-Q", "qqq,www"])
    )


def test_celery_without_app() -> None:
    assert get_python_app_id(process_with_cmdline(["celery", "a", "b"])) is None


def test_pyspark() -> None:
    assert "pyspark" == get_python_app_id(process_with_cmdline(["python", "-m", "pyspark.daemon"]))


def test_python() -> None:
    # python -m & different python bins
    assert "python: -m myapp" == get_python_app_id(process_with_cmdline(["python", "-m", "myapp"]))
    assert "python: -m myapp" == get_python_app_id(process_with_cmdline(["python3", "-m", "myapp"]))
    assert "python: -m myapp" == get_python_app_id(process_with_cmdline(["python3.8", "-m", "myapp"]))
    assert "python: -m myapp" == get_python_app_id(process_with_cmdline(["python2", "-m", "myapp"]))
    assert "python: -m myapp.x.y" == get_python_app_id(process_with_cmdline(["python2.7", "-m", "myapp.x.y"]))
    # python mod.py
    assert "python: /path/to/mod.py (/path/to/mod.py)" == get_python_app_id(
        process_with_cmdline(["python2.7", "/path/to/mod.py"])
    )
    assert f"python: mod.py ({PROCESS_CWD}/mod.py)" == get_python_app_id(process_with_cmdline(["python2.7", "mod.py"]))


def test_node_appid() -> None:
    assert f"nodejs: myapp.js ({PROCESS_CWD}/myapp.js)" == get_node_app_id(process_with_cmdline(["node", "myapp.js"]))
    assert f"nodejs: myapp/myapp.js ({PROCESS_CWD}/myapp/myapp.js)" == get_node_app_id(
        process_with_cmdline(["node", "myapp/myapp.js"])
    )
    assert f"nodejs: myapp.js ({PROCESS_CWD}/myapp.js)" == get_node_app_id(
        process_with_cmdline(["node", "myapp.js", "-r", "mock"])
    )
    assert "nodejs: /path/to/myapp.js (/path/to/myapp.js)" == get_node_app_id(
        process_with_cmdline(["node", "/path/to/myapp.js"])
    )
    assert f"nodejs: myapp.js ({PROCESS_CWD}/myapp.js)" == get_node_app_id(
        process_with_cmdline(["node", "--myflag", "myapp.js"])
    )
    assert f"nodejs: myapp.js ({PROCESS_CWD}/myapp.js)" == get_node_app_id(
        process_with_cmdline(["node", "-r", "myrequire.js", "--myflag", "myapp.js"])
    )
    assert f"nodejs: myapp.js ({PROCESS_CWD}/myapp.js)" == get_node_app_id(
        process_with_cmdline(["node", "--require=myrequire.js", "myapp.js"])
    )


def test_ruby_appid() -> None:
    assert f"ruby: myapp.rb ({PROCESS_CWD}/myapp.rb)" == get_ruby_app_id(process_with_cmdline(["ruby", "myapp.rb"]))
    assert "ruby: /path/to/myapp.rb (/path/to/myapp.rb)" == get_ruby_app_id(
        process_with_cmdline(["ruby", "/path/to/myapp.rb"])
    )
    assert f"ruby: myapp.rb ({PROCESS_CWD}/myapp.rb)" == get_ruby_app_id(
        process_with_cmdline(["ruby", "--myflag", "myapp.rb"])
    )
    assert f"ruby: myapp.rb ({PROCESS_CWD}/myapp.rb)" == get_ruby_app_id(
        process_with_cmdline(["ruby", "-r", "myrequire.rb", "--myflag", "myapp.rb"])
    )
    assert f"ruby: myapp.rb ({PROCESS_CWD}/myapp.rb)" == get_ruby_app_id(
        process_with_cmdline(["ruby", "-rmyrequire.rb", "--myflag", "myapp.rb"])
    )
