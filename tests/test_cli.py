import errno
import os
import signal
import socket
import subprocess
import signal as _signal
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest
from http.server import ThreadingHTTPServer

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


@pytest.mark.parametrize("flag,val", [("--port", "-1"), ("--port", "70000")])
def test_run_rejects_bad_numbers(flag, val):
    with pytest.raises(SystemExit) as e:
        main(["run", flag, val, "--no-browser"])
    assert e.value.code == 2


@pytest.mark.parametrize("argv", [["export"], ["status"], ["run", "--db", "x.db"], ["run", "--interval", "5"],
                                  ["run", "--retention-days", "3"], []])
def test_cli_rejects_removed(argv):
    with pytest.raises(SystemExit) as e:
        main(argv)
    assert e.value.code == 2


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _spawn(home, port):
    env = {k: v for k, v in os.environ.items() if not k.startswith("XDG_")}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1",
               PYTHONPATH=str(Path(__file__).resolve().parents[1]))
    return subprocess.Popen([sys.executable, "-m", "rms.cli", "run", "--no-browser", "--port", str(port)],
                            cwd=home, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _get(port):
    rq = urllib.request.Request(f"http://127.0.0.1:{port}/api/live",
                                headers={"Host": f"127.0.0.1:{port}"})
    return urllib.request.urlopen(rq, timeout=2).status


def test_run_writes_nothing(tmp_path):
    home = tmp_path / "home"
    home.mkdir()
    port = _free_port()
    p = _spawn(home, port)
    try:
        got = []
        for _ in range(12):
            time.sleep(1)
            try:
                got.append(_get(port))
            except OSError:
                pass
        assert got.count(200) >= 2
    finally:
        p.send_signal(signal.SIGINT)
        out, err = p.communicate(timeout=20)
    assert p.returncode == 0, err
    assert list(home.rglob("*")) == []


def test_run_sigterm_exits_cleanly(tmp_path):
    port = _free_port()
    p = _spawn(tmp_path, port)
    try:
        for _ in range(20):
            time.sleep(0.5)
            try:
                if _get(port) == 200:
                    break
            except OSError:
                pass
        else:
            pytest.fail("server never answered")
        p.send_signal(signal.SIGTERM)
        out, err = p.communicate(timeout=20)
    finally:
        if p.poll() is None:
            p.kill()
            p.communicate()
    assert p.returncode == 0, err


def test_no_background_threads(monkeypatch):
    baseline = set(threading.enumerate())
    seen = []
    monkeypatch.setattr(ThreadingHTTPServer, "serve_forever",
                        lambda self, *a, **k: seen.append(set(threading.enumerate())))
    old = _signal.getsignal(_signal.SIGTERM)
    try:
        assert main(["run", "--no-browser", "--port", str(_free_port())]) == 0
    finally:
        _signal.signal(_signal.SIGTERM, old)
    assert seen == [baseline]
