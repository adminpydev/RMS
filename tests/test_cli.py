import json
import socket

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
    assert "0 rows" in capsys.readouterr().out
