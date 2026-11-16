import errno
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

import rms.cli as cli
from rms.cli import main


def test_run_port_busy(capsys):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    try:
        assert main(["run", "--port", str(p), "--no-browser"]) == 2
    finally:
        s.close()
    assert "--port" in capsys.readouterr().err


def test_run_other_oserror(capsys, monkeypatch):
    def boom(*a):
        raise OSError(errno.EACCES, "denied")
    monkeypatch.setattr(cli, "make_server", boom)
    assert main(["run", "--no-browser"]) == 2
    err = capsys.readouterr().err
    assert "Cannot start server" in err and "in use" not in err


@pytest.mark.parametrize("flag,val", [("--interval", "0"), ("--port", "-1"), ("--port", "70000")])
def test_run_rejects_bad_numbers(flag, val):
    with pytest.raises(SystemExit) as e:
        main(["run", flag, val, "--no-browser"])
    assert e.value.code == 2


@pytest.mark.parametrize("argv", [["export"], ["status"], ["run", "--db", "x.db"],
                                  ["run", "--retention-days", "3"], []])
def test_cli_rejects_removed(argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2


def test_run_writes_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    p = subprocess.Popen([sys.executable, "-m", "rms.cli", "run", "--no-browser", "--port", "8791"],
                         cwd=home, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        got = []
        for _ in range(12):
            time.sleep(1)
            try:
                rq = urllib.request.Request("http://127.0.0.1:8791/api/live?fast=1",
                                            headers={"Host": "127.0.0.1:8791"})
                got.append(urllib.request.urlopen(rq, timeout=2).status)
            except OSError:
                pass
        assert got.count(200) >= 2
    finally:
        p.send_signal(signal.SIGINT)
        out, err = p.communicate(timeout=20)
    assert p.returncode == 0, err
    assert list(home.rglob("*")) == []
