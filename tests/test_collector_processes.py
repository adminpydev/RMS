import psutil
import pytest

from rms import collector
from rms.collector import Collector, ProcessTracker, sample_processes
from rms.live import LiveSampler, Recent

MiB = 2**20


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


class Store:
    def __init__(self):
        self.procs, self.samples, self.touched, self.prune_ids = [], [], [], []
        self.last_pruned_ids = []

    def write_system(self, *a):
        pass

    def write_apps(self, *a):
        pass

    def add_processes(self, rows, now):
        ids = []
        for r in rows:
            self.procs.append(r)
            ids.append(len(self.procs))
        return ids

    def write_process_samples(self, ts, rows):
        self.samples.append((ts, rows))

    def touch_processes(self, ts, ids):
        self.touched.append((ts, sorted(ids)))

    def prune(self, before):
        self.last_pruned_ids = self.prune_ids
        return 0


@pytest.fixture
def env(monkeypatch):
    state = {"procs": [], "iter_calls": 0}

    def it(attrs=None):
        state["iter_calls"] += 1
        return iter(state["procs"])

    vm = type("VM", (), {"total": 1000, "used": 400, "available": 600})()
    monkeypatch.setattr(psutil, "virtual_memory", lambda: vm)
    monkeypatch.setattr(psutil, "swap_memory", lambda: type("S", (), {"used": 5})())
    monkeypatch.setattr(psutil, "process_iter", it)
    monkeypatch.setattr(collector, "mac_extras", lambda: (1, 9))
    monkeypatch.setattr(collector.sys, "platform", "darwin")
    return state


def test_process_tracker_hysteresis():
    t = ProcessTracker(top=1, threshold=1000, hold=3600)
    A, B, C = (1, 1), (2, 1), (3, 1)
    assert t.select(0, {A: 2000}) == {A}
    assert t.select(10, {A: 10, B: 50}) == {A, B}
    assert t.select(3610, {A: 10, B: 50, C: 5}) == {B}


def test_sample_processes_shape():
    p = P(7, 500, start=100)
    got = sample_processes([p], "darwin")
    assert got == {(7, 100): {"pid": 7, "ppid": 1, "start_ts": 100, "name": "proc7", "app": "proc7",
                              "rss": 500, "threads": 3, "proc": p}}


def test_metadata_read_once(env):
    big = [P(i, 100 * MiB) for i in range(1, 4)]
    small = [P(i, 1) for i in range(10, 70)]
    env["procs"] = big + small
    st = Store()
    c = Collector(st)
    for t in (1000, 1010, 1020):
        c.tick(t)
    tracked = [p for p in env["procs"] if p.cmd_calls]
    assert all(p.cmd_calls == 1 and p.user_calls == 1 for p in tracked)
    assert set(big) <= set(tracked) and len(tracked) == 50
    assert len(st.procs) == 50
    first = [r for ts, r in st.samples if ts == 1000][0]
    assert all(cpu is None for _, _, cpu, _ in first)  # first sight -> no cpu
    later = [r for ts, r in st.samples if ts == 1010][0]
    assert all(cpu == 123 for _, _, cpu, _ in later)


def test_access_denied_meta_null(env):
    env["procs"] = [P(1, 100 * MiB, deny=True), P(2, 100 * MiB, cmd=("a", "b"), user="bob")]
    st = Store()
    Collector(st).tick(1000)
    rows = {r[0]: r for r in st.procs}
    assert rows[1][5:] == (None, None)
    assert rows[2][5:] == ("bob", "a b")


def test_single_process_iter_per_tick(env):
    env["procs"] = [P(1, 100 * MiB)]
    c = Collector(Store())
    c.tick(1000)
    c.tick(1010)
    assert env["iter_calls"] == 2


def test_touch_alive_and_prune_map(env):
    p = P(1, 100 * MiB)
    env["procs"] = [p] + [P(i, 200 * MiB) for i in range(2, 52)]
    st = Store()
    c = Collector(st)
    c.tick(1000)
    pid1 = c.proc_ids[(1, 100)]
    p.info["memory_info"].rss = 1  # drops out of tracking after hold
    c.tick(1000 + 3600)
    assert (1, 100) not in c.tracker.last_pass
    assert pid1 in st.touched[-1][1]  # alive but untracked -> still touched
    assert pid1 not in [r[0] for r in st.samples[-1][1]]
    st.prune_ids = [pid1]
    c.last_prune = 0
    c.tick(1000 + 7200)
    assert (1, 100) not in c.proc_ids


def push(r, ts, procs, swap=0):
    r.push(ts, {"total": 1000, "used": 400, "available": 600, "swap_used": swap, "pressure": 1},
           {k: {"pid": k[0], "ppid": 1, "start_ts": k[1], "name": "n", "app": "a",
                "rss": v, "cpu": None, "threads": 2, "proc": P(k[0], v)} for k, v in procs.items()})


def test_recent_snapshot_shape():
    r = Recent()
    old, new = (1, 1), (2, 2)
    for ts in range(0, 361, 2):
        procs = {old: 1000 + ts}
        procs.update({(100 + i, 1): 10 * i for i in range(80)})
        if ts >= 300:
            procs[new] = 5
        push(r, ts, procs, swap=ts)
    s = r.snapshot({old: 9})
    assert s["ts"] == 360 and s["resolution"] == 2 and s["simulated"] is False
    assert s["system"]["swap_used_10m_ago"] is None and s["system"]["swap_used"] == 360
    rows = {(p["pid"], p["start_ts"]): p for p in s["processes"]}
    assert rows[old]["growth_5m"] == 300 and rows[old]["id"] == 9
    assert rows[new]["growth_5m"] is None and rows[new]["id"] is None
    assert set(rows[old]) == {"id", "pid", "ppid", "start_ts", "name", "app", "user", "cmdline",
                              "rss", "cpu", "threads", "growth_5m", "spark"}
    sparked = [p for p in s["processes"] if p["spark"] is not None]
    assert len(sparked) <= 70 and all(len(p["spark"]) <= 30 for p in sparked)
    assert rows[(100, 1)]["spark"] is None  # rss 0, growth 0: not top-50, not top-20 growth
    assert rows[old]["spark"] and rows[old]["spark"][-1] == 1360
    for ts in range(362, 601, 2):
        push(r, ts, {old: 1}, swap=ts)
    assert r.snapshot({})["system"]["swap_used_10m_ago"] == 0
    push(r, 610, {old: 1}, swap=610)
    s = r.snapshot({})
    assert s["resolution"] == 10
    assert s["system"]["swap_used_10m_ago"] == 10  # entries older than 600 s pruned
    assert (100, 1) in r.meta
    push(r, 961, {old: 1})  # last entry with the others (ts 360) now > 600 s old
    assert set(r.meta) == {old} and set(r._procs) == {old}


def test_live_cmdline_redacted():
    r = Recent()
    push(r, 0, {(1, 1): 10})
    r._procs[(1, 1)]._cmd = ["app", "--token=x"]
    row = r.snapshot({})["processes"][0]
    assert row["cmdline"] == "app --token=***" and row["user"] == "me"
    r.snapshot({})
    assert r._procs[(1, 1)].cmd_calls == 1  # cached once per key


def test_live_sampler_idles():
    clock = [0.0]

    class C:
        calls = 0

        def scan_live(self, now):
            C.calls += 1

    class Stop:
        def __init__(self, n):
            self.n = n

        def is_set(self):
            return self.n <= 0

        def wait(self, dt):
            self.n -= 1
            clock[0] += dt

    s = LiveSampler(Recent(), C(), clock=lambda: clock[0])
    s.run(Stop(5))
    assert C.calls == 0  # never poked
    s.poke()
    s.run(Stop(5))
    assert C.calls == 5  # every 2 s
    s.run(Stop(10))  # clock 20 → 40
    assert C.calls == 8  # 20, 22, 24 s (< 15 s after poke at 10), then idle
