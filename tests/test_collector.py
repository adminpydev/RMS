import psutil
import pytest

from rms import collector
from rms.collector import Collector, app_name, sample_processes
from rms.live import Recent

MiB = 2**20
CHROME_EXE = ("/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/"
              "Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)")


@pytest.mark.parametrize("name,exe,plat,want", [
    ("Google Chrome Helper (Renderer)", CHROME_EXE, "darwin", "Google Chrome"),
    ("Code Helper", "/Applications/Visual Studio Code.app/Contents/MacOS/Code Helper", "darwin", "Visual Studio Code"),
    ("python3", "/usr/bin/python3", "darwin", "python3"),
    ("chrome.exe", "C:\\Program Files\\Google\\Chrome\\chrome.exe", "win32", "chrome"),
    ("firefox", "/usr/lib/firefox/firefox", "linux", "firefox"),
    ("kernel_task", None, "darwin", "kernel_task"),
])
def test_app_name_rules(name, exe, plat, want):
    assert app_name(name, exe, plat) == want


class P:
    """Fake psutil.Process with .info and the per-process calls the collector makes."""

    def __init__(self, pid, rss, name=None, start=100, ppid=1, threads=3,
                 cmd=("prog", "--x"), user="me", deny=False):
        self.pid = pid
        self.info = {"pid": pid, "ppid": ppid, "name": name or "proc%d" % pid, "exe": None,
                     "memory_info": type("M", (), {"rss": rss})(), "create_time": start + 0.5,
                     "num_threads": threads}
        self._cmd, self._user, self._deny = list(cmd), user, deny
        self.cmd_calls = self.user_calls = 0

    def cmdline(self):
        self.cmd_calls += 1
        if self._deny:
            raise psutil.AccessDenied(self.pid)
        return self._cmd

    def username(self):
        self.user_calls += 1
        if self._deny:
            raise psutil.AccessDenied(self.pid)
        return self._user

    def cpu_percent(self, interval=None):
        return 12.34


class Raises:
    def __init__(self, exc):
        self.exc = exc

    @property
    def info(self):
        raise self.exc


@pytest.fixture
def env(monkeypatch):
    state = {"procs": [], "iter_calls": 0, "extras_calls": 0}

    def it(attrs=None):
        state["iter_calls"] += 1
        return iter(state["procs"])

    def extras():
        state["extras_calls"] += 1
        return (2, 9)

    vm = type("VM", (), {"total": 1000, "used": 400, "available": 600})()
    monkeypatch.setattr(psutil, "virtual_memory", lambda: vm)
    monkeypatch.setattr(psutil, "swap_memory", lambda: type("S", (), {"used": 5})())
    monkeypatch.setattr(psutil, "process_iter", it)
    monkeypatch.setattr(collector, "mac_extras", extras)
    monkeypatch.setattr(collector.sys, "platform", "darwin")
    return state


def test_sample_processes_shape():
    p = P(7, 500, start=100)
    got = sample_processes([p], "darwin")
    assert got == {(7, 100): {"pid": 7, "ppid": 1, "start_ts": 100, "name": "proc7", "app": "proc7",
                              "rss": 500, "threads": 3, "proc": p}}


def test_sample_processes_skips_errors():
    procs = [Raises(psutil.AccessDenied()), Raises(psutil.NoSuchProcess(1)), P(2, 1),
             P(3, 7, name="", cmd=())]
    procs[2].info["memory_info"] = None
    procs[3].info["name"] = None
    assert sample_processes(procs, "darwin") == {}


def test_tick_pushes_into_recent(env):
    env["procs"] = [P(1, 100 * MiB), P(2, 5 * MiB)]
    r = Recent()
    c = Collector(r)
    c.tick(1000)
    c.tick(1010)
    c.tick(1030)
    assert env["iter_calls"] == 3  # single process_iter per tick
    assert env["extras_calls"] == 2  # mac extras every 30 s, reused between
    s = r.snapshot()
    assert s["ts"] == 1030 and s["system"]["pressure"] == 2 and s["system"]["used"] == 400
    rows = {p["pid"]: p for p in s["processes"]}
    assert rows[1]["rss"] == 100 * MiB and rows[1]["threads"] == 3
    assert [e[2][(1, 100)][1] for e in r.entries] == [None, 123, 123]  # first cpu discarded


def test_meta_read_lazily_once(env):
    env["procs"] = [P(i, 100 * MiB) for i in range(1, 4)]
    r = Recent()
    c = Collector(r)
    for t in (1000, 1010, 1020):
        c.tick(t)
    assert all(p.cmd_calls == 0 for p in env["procs"])  # nothing read until asked
    r.snapshot()
    r.snapshot()
    assert all(p.cmd_calls == 1 and p.user_calls == 1 for p in env["procs"])


def test_access_denied_meta_null(env):
    env["procs"] = [P(1, 100 * MiB, deny=True), P(2, 100 * MiB, cmd=("a", "b"), user="bob")]
    r = Recent()
    Collector(r).tick(1000)
    rows = {p["pid"]: p for p in r.snapshot()["processes"]}
    assert (rows[1]["user"], rows[1]["cmdline"]) == (None, None)
    assert (rows[2]["user"], rows[2]["cmdline"]) == ("bob", "a b")


def test_scan_live_pushes(env):
    env["procs"] = [P(1, 100 * MiB)]
    r = Recent()
    Collector(r).scan_live(1000)
    assert r.snapshot()["ts"] == 1000 and env["extras_calls"] == 0


def test_tick_skips_scan_after_recent_live_scan(env):
    env["procs"] = [P(1, 100 * MiB)]
    c = Collector(Recent())
    c.scan_live(999)  # live sampler
    c.tick(1000)
    assert env["iter_calls"] == 1 and env["extras_calls"] == 1  # scan skipped, extras still read
    c.tick(1010)
    assert env["iter_calls"] == 2


def test_ps_cache_pruned_when_process_gone(env):
    env["procs"] = [P(1, 100 * MiB), P(2, 5 * MiB)]
    c = Collector(Recent())
    c.scan_live(1000)
    assert set(c._ps) == {(1, 100), (2, 100)}
    env["procs"] = env["procs"][:1]
    c.scan_live(1010)
    assert set(c._ps) == {(1, 100)}
