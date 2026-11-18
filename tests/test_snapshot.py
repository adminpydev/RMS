import os
import socket
import sys
import threading
from collections import namedtuple

import psutil
import pytest

from rms import snapshot
from rms.snapshot import Snapshotter, app_name

CHROME_EXE = ("/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/"
              "Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)")
Conn = namedtuple("Conn", "type status laddr")
Addr = namedtuple("Addr", "ip port")


@pytest.mark.parametrize("name,exe,plat,want", [
    ("Google Chrome Helper (Renderer)", CHROME_EXE, "darwin", "Google Chrome"),
    ("python3", "/usr/bin/python3", "darwin", "python3"),
    ("chrome.exe", "C:\\Program Files\\Google\\Chrome\\chrome.exe", "win32", "chrome"),
    ("firefox", "/usr/lib/firefox/firefox", "linux", "firefox"),
    ("kernel_task", None, "darwin", "kernel_task"),
])
def test_app_name_rules(name, exe, plat, want):
    assert app_name(name, exe, plat) == want


class P:
    def __init__(self, pid, rss, name=None, exe=None, start=100, ppid=1, cmd=("prog", "--x"), deny=(),
                 cpu=12.34):
        self.pid = pid
        self.info = {"pid": pid, "ppid": ppid, "name": name or "proc%d" % pid, "exe": exe,
                     "username": "me", "memory_info": type("M", (), {"rss": rss})(),
                     "num_threads": 3, "status": "running", "create_time": start + 0.5}
        self._cmd, self._deny, self._cpu = list(cmd), set(deny), cpu
        if rss is None:
            self.info["memory_info"] = None  # what process_iter gives for a denied attr
        self.cmd_calls = 0

    def _chk(self, what):
        if what in self._deny:
            raise psutil.AccessDenied(self.pid)

    def cmdline(self):
        self.cmd_calls += 1
        self._chk("cmdline")
        return self._cmd

    def cpu_percent(self, interval=None):
        return self._cpu


@pytest.fixture
def env(monkeypatch):
    state = {"procs": []}
    vm = type("VM", (), {"total": 1000, "used": 400, "available": 600, "free": 50,
                         "active": 300, "inactive": 200, "wired": 100})()
    monkeypatch.setattr(psutil, "virtual_memory", lambda: vm)
    monkeypatch.setattr(psutil, "swap_memory", lambda: type("S", (), {"total": 8, "used": 5})())
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(state["procs"]))
    monkeypatch.setattr(snapshot, "mac_extras", lambda: (1, 150))
    state["vm"] = vm
    return state


def test_take_shape():
    s = Snapshotter()
    out = s.take()
    assert set(out) == {"ts", "simulated", "system", "apps", "processes"}
    assert out["simulated"] is False
    assert set(out["system"]) == {"total", "used", "available", "swap_total", "swap_used",
                                  "pressure", "breakdown"}
    row = next(r for r in out["processes"] if r["pid"] == os.getpid())
    assert set(row) == {"pid", "ppid", "start_ts", "name", "app", "user", "cmdline", "rss",
                        "share", "cpu", "threads", "status"}
    assert row["cpu"] is None and 0 < row["share"] < 1
    assert set(out["apps"][0]) == {"app", "rss", "share", "procs"}


def test_breakdown_sums_to_total_macos(env):
    b = Snapshotter(platform="darwin").take()["system"]["breakdown"]
    assert b == {"wired": 100, "active": 300, "inactive": 200, "compressed": 150, "free": 250}
    assert sum(b.values()) == 1000


def test_breakdown_macos_free_never_negative(env, monkeypatch):
    monkeypatch.setattr(snapshot, "mac_extras", lambda: (None, None))
    env["vm"].active = 900
    s = Snapshotter(platform="darwin").take()["system"]
    assert s["breakdown"]["compressed"] == 0 and s["breakdown"]["free"] == 0
    assert s["pressure"] is None


def test_breakdown_non_macos(env):
    s = Snapshotter(platform="linux").take()["system"]
    assert s["breakdown"] == {"used": 400, "free": 50}
    assert s["pressure"] is None
    env["vm"].cached = 77
    assert Snapshotter(platform="linux").take()["system"]["breakdown"] == {
        "used": 400, "cached": 77, "free": 50}


def test_mac_extras_cached_5s(env, monkeypatch):
    calls, now = [], [0.0]
    monkeypatch.setattr(snapshot, "mac_extras", lambda: calls.append(1) or (1, 150))
    s = Snapshotter(platform="darwin", clock=lambda: now[0])
    s.take(); now[0] = 4.9; s.take()
    assert len(calls) == 1
    now[0] = 5.0; s.take()
    assert len(calls) == 2


def test_apps_grouping_and_share(env):
    env["procs"] = [P(1, 300, exe="/Applications/A.app/Contents/MacOS/a"),
                    P(2, 100, exe="/Applications/A.app/Contents/MacOS/h"),
                    P(3, 333, name="b")]
    out = Snapshotter(platform="darwin").take()
    assert out["apps"] == [{"app": "A", "rss": 400, "share": 0.4, "procs": 2},
                           {"app": "b", "rss": 333, "share": 0.333, "procs": 1}]
    assert {r["pid"]: r["share"] for r in out["processes"]} == {1: 0.3, 2: 0.1, 3: 0.333}


def test_cpu_null_then_number(env):
    env["procs"] = [P(1, 10)]
    s = Snapshotter(platform="linux")
    assert s.take()["processes"][0]["cpu"] is None
    assert s.take()["processes"][0]["cpu"] == 123


def test_cpu_rounded(env):
    env["procs"] = [P(1, 10, cpu=12.36)]
    s = Snapshotter(platform="linux")
    s.take()
    assert s.take()["processes"][0]["cpu"] == 124


def test_memory_denied_row_kept(env):
    env["procs"] = [P(1, 100, name="a"), P(2, None, name="a"), P(3, 300, name="b"),
                    P(4, 10, name="c")]
    env["procs"][3].info["create_time"] = None  # can't key: skipped
    out = Snapshotter(platform="linux").take()
    assert [r["pid"] for r in out["processes"]] == [3, 1, 2]  # rss desc, nulls last
    denied = out["processes"][2]
    assert denied["rss"] is None and denied["share"] is None
    assert out["apps"] == [{"app": "b", "rss": 300, "share": 0.3, "procs": 1},
                           {"app": "a", "rss": 100, "share": 0.1, "procs": 2}]


def test_cache_pruned(env):
    a, b = P(1, 10), P(2, 10)
    env["procs"] = [a, b]
    s = Snapshotter(platform="linux")
    s.take()
    env["procs"] = [a]
    s.take()
    assert list(s._cache) == [(1, 100)]
    assert a.cmd_calls == 1  # read once per key


def test_cmdline_redacted_capped(env):
    env["procs"] = [P(1, 10, cmd=("prog", "--password=hunter2")),
                    P(2, 10, cmd=("x" * 5000,)), P(3, 10, deny={"cmdline"})]
    rows = {r["pid"]: r for r in Snapshotter(platform="linux").take()["processes"]}
    assert "hunter2" not in rows[1]["cmdline"] and "***" in rows[1]["cmdline"]
    assert len(rows[2]["cmdline"]) == 2000
    assert rows[3]["cmdline"] is None


def test_process_detail_own_process():
    me = psutil.Process()
    d = Snapshotter().process_detail(os.getpid(), int(me.create_time()))
    for k in ("uss", "vms", "cpu_user", "open_files", "ports", "rss", "cwd", "exe"):
        assert k in d and d[k] is not None, k
    assert d["restricted"] is False
    assert d["parent"] is None or d["parent"]["pid"] == os.getppid()


class FakeProc:
    def __init__(self, pid=50, start=100, deny=(), gone=(), conns=()):
        self.pid, self._start, self._deny, self._gone, self._conns = pid, start, set(deny), set(gone), conns

    def _r(self, what, val):
        if what in self._gone:
            raise psutil.NoSuchProcess(self.pid)
        if what in self._deny:
            raise psutil.AccessDenied(self.pid)
        return val

    def create_time(self): return self._r("create_time", self._start + 0.7)
    def name(self): return "fake"
    def ppid(self): return 1
    def exe(self): return self._r("exe", "/bin/fake")
    def cwd(self): return self._r("cwd", "/")
    def username(self): return "other"
    def cmdline(self): return self._r("cmdline", ["fake", "--token=abc"])
    def status(self): return "running"
    def num_threads(self): return 2

    def memory_info(self):
        return type("M", (), {"rss": 10, "vms": 20, "pfaults": 3, "pageins": 4})()

    def memory_full_info(self): return self._r("uss", type("F", (), {"uss": 7})())
    def cpu_times(self): return self._r("cpu_times", type("T", (), {"user": 1.5, "system": 0.5})())
    def open_files(self): return self._r("open_files", [1, 2, 3])
    def net_connections(self, kind="inet"): return self._r("net", list(self._conns))


@pytest.fixture
def fake_table(monkeypatch):
    def install(proc, table=()):
        monkeypatch.setattr(psutil, "Process", lambda pid: proc if pid == proc.pid else
                            (_ for _ in ()).throw(psutil.NoSuchProcess(pid)))
        rows = [type("I", (), {"info": {"pid": p, "ppid": pp, "name": n, "create_time": st}})()
                for p, pp, n, st in table]
        monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: iter(rows))
    return install


def test_process_restricted(fake_table):
    fake_table(FakeProc(deny={"uss", "open_files", "net", "cwd"}))
    d = Snapshotter(platform="linux").process_detail(50, 100)
    assert d["uss"] is None and d["open_files"] is None and d["ports"] is None and d["cwd"] is None
    assert d["restricted"] is True
    assert d["rss"] == 10 and d["cpu_user"] == 1.5 and d["exe"] == "/bin/fake"
    assert "abc" not in d["cmdline"]


def test_process_negative_pid_none():
    assert Snapshotter().process_detail(-1, 100) is None


def test_process_detail_reuses_recent_take_table(env, fake_table, monkeypatch):
    now = [0.0]
    s = Snapshotter(platform="linux", clock=lambda: now[0])
    env["procs"] = [P(1, 10, name="init", start=50), P(50, 10, start=100),
                    P(60, 10, name="kid", ppid=50, start=120)]
    s.take()
    fake_table(FakeProc(), table=[(1, 0, "other-init", 50.0)])  # scan would see no kid
    now[0] = 4.9
    d = s.process_detail(50, 100)
    assert d["parent"] == {"pid": 1, "start_ts": 50, "name": "init"}
    assert d["children"] == [{"pid": 60, "start_ts": 120, "name": "kid"}]
    now[0] = 5.0  # stale: own scan
    d = s.process_detail(50, 100)
    assert d["parent"]["name"] == "other-init" and d["children"] == []


def test_process_start_ts_mismatch_404(fake_table):
    fake_table(FakeProc())
    assert Snapshotter().process_detail(50, 101) is None
    assert Snapshotter().process_detail(51, 100) is None


def test_process_gone(fake_table):
    fake_table(FakeProc(gone={"open_files"}))
    assert Snapshotter().process_detail(50, 100) is None


def test_process_parent_children_pid_reuse(fake_table):
    fake_table(FakeProc(), table=[(1, 0, "init", 150.0),  # parent started after child: reused pid
                                  (50, 1, "fake", 100.7),
                                  (60, 50, "kid", 120.0), (61, 50, "old", 90.0)])
    d = Snapshotter().process_detail(50, 100)
    assert d["parent"] is None
    assert d["children"] == [{"pid": 60, "start_ts": 120, "name": "kid"}]


def test_ports_listening_only_dedup_cap(fake_table):
    T, U = socket.SOCK_STREAM, socket.SOCK_DGRAM
    conns = [Conn(T, psutil.CONN_LISTEN, Addr("127.0.0.1", 8080)),
             Conn(T, psutil.CONN_LISTEN, Addr("127.0.0.1", 8080)),  # dup
             Conn(T, psutil.CONN_ESTABLISHED, Addr("127.0.0.1", 5000)),
             Conn(U, psutil.CONN_NONE, Addr("0.0.0.0", 53)),
             Conn(U, psutil.CONN_NONE, ()),  # unbound udp
             Conn(U, psutil.CONN_NONE, Addr("0.0.0.0", 0))]  # macOS unbound udp
    conns += [Conn(T, psutil.CONN_LISTEN, Addr("::", 10000 + i)) for i in range(60)]
    fake_table(FakeProc(conns=conns))
    ports = Snapshotter().process_detail(50, 100)["ports"]
    assert len(ports) == 50
    assert ports[:2] == [{"port": 53, "proto": "udp", "addr": "0.0.0.0"},
                         {"port": 8080, "proto": "tcp", "addr": "127.0.0.1"}]
    assert all(p["port"] not in (0, 5000) for p in ports)


def test_take_concurrent():
    s, errors, keys = Snapshotter(), [], []

    def work():
        try:
            for _ in range(10):
                out = s.take()
                keys.append((tuple(sorted(out)), tuple(sorted(out["system"]))))
        except Exception as e:  # pragma: no cover
            errors.append(e)

    ts = [threading.Thread(target=work) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and len(keys) == 20 and len(set(keys)) == 1
