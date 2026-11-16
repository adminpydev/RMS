import json
import socket

import pytest

from rms.cli import main
from rms.store import Store


def test_run_port_busy(tmp_path, capsys):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    try:
        assert main(["run", "--port", str(p), "--no-browser", "--db", str(tmp_path / "r.db")]) == 2
    finally:
        s.close()
    assert "--port" in capsys.readouterr().err


def test_export_stdout_format(tmp_path, capsys):
    db = str(tmp_path / "r.db")
    st = Store(db)
    for ts in (100, 110):
        st.write_system(ts, 1000, 500, 500, None, None, 0, None)
    assert main(["export", "--db", db]) == 0
    d = json.loads(capsys.readouterr().out)
    assert {"meta", "system", "apps"} <= set(d)
    assert d["meta"]["relative"] is False
    assert {"host", "os", "interval", "version"} <= set(d["meta"])


def test_status_empty(tmp_path, capsys):
    assert main(["status", "--db", str(tmp_path / "r.db")]) == 0
    out = capsys.readouterr().out
    assert "0 rows" in out
    assert "processes: 0 rows" in out and "process samples: 0 rows" in out


def test_run_other_oserror_and_store_closed(tmp_path, capsys, monkeypatch):
    import errno
    import rms.cli as cli
    closed = []
    orig = Store.close
    monkeypatch.setattr(Store, "close", lambda self: (closed.append(1), orig(self)))

    def boom(*a):
        raise OSError(errno.EACCES, "denied")
    monkeypatch.setattr(cli, "make_server", boom)
    assert main(["run", "--no-browser", "--db", str(tmp_path / "r.db")]) == 2
    err = capsys.readouterr().err
    assert "Cannot start server" in err and "in use" not in err
    assert closed


def test_export_bad_iso(tmp_path, capsys):
    import pytest
    with pytest.raises(SystemExit) as e:
        main(["export", "--db", str(tmp_path / "r.db"), "--from", "nope"])
    assert e.value.code == 2
    assert "invalid ISO" in capsys.readouterr().err


@pytest.mark.parametrize("flag,val", [("--interval", "0"), ("--retention-days", "0"), ("--port", "-1"), ("--port", "70000")])
def test_run_rejects_bad_numbers(flag, val, tmp_path):
    with pytest.raises(SystemExit) as e:
        main(["run", flag, val, "--no-browser", "--db", str(tmp_path / "r.db")])
    assert e.value.code == 2
