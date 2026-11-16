from rms.live import LiveSampler, Recent

MiB = 2**20


class P:
    """Fake psutil.Process: the lazy cmdline/username reads Recent makes."""

    def __init__(self, cmd=("prog", "--x"), user="me"):
        self._cmd, self._user = list(cmd), user
        self.cmd_calls = 0

    def cmdline(self):
        self.cmd_calls += 1
        return self._cmd

    def username(self):
        return self._user


def push(r, ts, procs, swap=0, meta=None):
    """procs: {key: rss}; meta: {key: {field: value}} overrides (ppid, name, cpu, ...)."""
    meta = meta or {}
    rows = {}
    for k, v in procs.items():
        row = {"pid": k[0], "ppid": 1, "start_ts": k[1], "name": "n", "app": "a",
               "rss": v, "cpu": None, "threads": 2, "proc": P()}
        row.update(meta.get(k, {}))
        rows[k] = row
    r.push(ts, {"total": 1000, "used": 400, "available": 600, "swap_used": swap, "pressure": 1},
           rows)


def test_recent_snapshot_shape():
    r = Recent()
    old, new = (1, 1), (2, 2)
    for ts in range(0, 361, 2):
        procs = {old: 1000 + ts}
        procs.update({(100 + i, 1): 10 * i for i in range(80)})
        if ts >= 300:
            procs[new] = 5
        push(r, ts, procs, swap=ts)
    s = r.snapshot()
    assert s["ts"] == 360 and s["resolution"] == 2 and s["simulated"] is False
    assert s["system"]["swap_used_10m_ago"] is None and s["system"]["swap_used"] == 360
    rows = {(p["pid"], p["start_ts"]): p for p in s["processes"]}
    # vs ts 58: newest kept entry of the 40-60 bucket (older than 60 s thinned to 20 s)
    assert rows[old]["growth_5m"] == 302
    assert rows[new]["growth_5m"] is None
    assert set(rows[old]) == {"pid", "ppid", "start_ts", "name", "app", "user", "cmdline",
                              "rss", "cpu", "threads", "growth_5m", "growth_30m", "trend",
                              "spark"}
    sparked = [p for p in s["processes"] if p["spark"] is not None]
    assert len(sparked) <= 70 and all(len(p["spark"]) <= 30 for p in sparked)
    assert rows[(100, 1)]["spark"] is None  # rss 0, growth 0: not top-50, not top-20 growth
    assert rows[old]["spark"] and rows[old]["spark"][-1] == 1360
    for ts in range(362, 601, 2):
        push(r, ts, {old: 1}, swap=ts)
    assert r.snapshot()["system"]["swap_used_10m_ago"] == 18  # closest to now-600: ts 18
    push(r, 610, {old: 1}, swap=610)
    s = r.snapshot()
    assert s["resolution"] == 10
    assert s["system"]["swap_used_10m_ago"] == 18  # still the closest to now-600
    assert (100, 1) in r.meta
    push(r, 2161, {old: 1})  # last entry with the others (ts 360) now > 1800 s old
    assert set(r.meta) == {old} and set(r._procs) == {old}


def test_recent_buffer_bounded():
    r = Recent()
    for ts in range(0, 600, 2):  # 300 pushes, 2 s apart
        push(r, ts, {(1, 1): ts}, swap=ts)
    ts = [e[0] for e in r.entries]
    assert len(ts) <= 60
    assert ts == sorted(ts) and all(t in ts for t in range(540, 600, 2))  # last 60 s complete
    s = r.snapshot()
    assert s["resolution"] == 2
    assert s["system"]["swap_used_10m_ago"] == 18
    row = s["processes"][0]
    assert abs(row["growth_5m"] - 300) <= 20
    assert 28 <= len(row["spark"]) <= 30
    assert all(0 < b - a <= 22 for a, b in zip(row["spark"], row["spark"][1:]))  # ~20 s spacing


def test_tiers_bounded():
    r = Recent()
    for ts in range(0, 1801, 2):
        push(r, ts, {(1, 1): ts})
    ts = [e[0] for e in r.entries]
    assert ts == sorted(ts) and len(ts) <= 30 + 27 + 20 + 5
    assert 1800 - ts[0] >= 1740
    assert all(t in ts for t in range(1742, 1801, 2))  # last 60 s complete
    mid = [t for t in ts if 60 < 1800 - t <= 600]
    assert all(b - a <= 22 for a, b in zip(mid, mid[1:]))
    far = [t for t in ts if 1800 - t > 600]
    assert all(55 <= b - a <= 65 for a, b in zip(far, far[1:]))


def ramp(r, mib, step=10, end=1800, key=(1, 1), at=None):
    """Push rss 1000 MiB rising linearly by `mib` over [0, end] (or a step at `at`)."""
    for ts in range(0, end + 1, step):
        grow = (mib if ts >= at else 0) if at is not None else mib * ts / end
        push(r, ts, {key: int((1000 + grow) * MiB)})


def test_growth_30m():
    r = Recent()
    ramp(r, 300)
    row = r.snapshot()["processes"][0]
    assert abs(row["growth_30m"] - 300 * MiB) <= 10 * MiB


def test_growth_30m_null_when_young():
    r = Recent()
    for ts in range(0, 1801, 10):
        procs = {(1, 1): 1000 * MiB}
        if ts >= 120:
            procs[(2, 120)] = int((100 + ts) * MiB)  # +1680 MiB, but only 28 min old
        push(r, ts, procs)
    rows = {p["pid"]: p for p in r.snapshot()["processes"]}
    assert rows[1]["growth_30m"] == 0 and rows[1]["trend"] is None
    assert rows[2]["growth_30m"] is None and rows[2]["trend"] is None
    assert rows[2]["growth_5m"] is not None


def test_trend_r2_linear_vs_step():
    r = Recent()
    ramp(r, 400)
    assert r.snapshot()["processes"][0]["trend"]["r2"] > 0.95
    r = Recent()
    ramp(r, 400, at=900)
    row = r.snapshot()["processes"][0]
    assert row["growth_30m"] > 200 * MiB and row["trend"]["r2"] < 0.8
    r = Recent()
    ramp(r, 400, step=120)  # 16 points over 30 min
    row = r.snapshot()["processes"][0]
    assert row["growth_30m"] > 200 * MiB and row["trend"] is None


def test_trend_only_when_growth_over_200():
    r = Recent()
    ramp(r, 150)
    row = r.snapshot()["processes"][0]
    assert row["growth_30m"] > 100 * MiB and row["trend"] is None


def test_recent_out_of_order_push():
    r = Recent()
    push(r, 1002, {(1, 1): 2})  # live sampler
    push(r, 1000, {(1, 1): 1})  # collector tick, computed earlier, pushed later
    assert [e[0] for e in r.entries] == [1000, 1002]
    s = r.snapshot()
    assert s["ts"] == 1002 and s["processes"][0]["rss"] == 2


def test_empty_snapshot():
    assert Recent().snapshot() == {"ts": None, "resolution": None, "simulated": False,
                                   "system": None, "processes": []}


def test_live_cmdline_capped():
    r = Recent()
    push(r, 0, {(1, 1): 10})
    r._procs[(1, 1)]._cmd = ["app", "x" * 5000]
    assert len(r.snapshot()["processes"][0]["cmdline"]) == 2000


def test_live_cmdline_redacted():
    r = Recent()
    push(r, 0, {(1, 1): 10})
    r._procs[(1, 1)]._cmd = ["app", "--token=x"]
    row = r.snapshot()["processes"][0]
    assert row["cmdline"] == "app --token=***" and row["user"] == "me"
    r.snapshot()
    assert r.process(1, 1)["cmdline"] == "app --token=***"
    assert r._procs[(1, 1)].cmd_calls == 1  # cached once per key


FAMILY = {(7, 50): {"name": "daddy"}, (9, 60): {"ppid": 7, "name": "kid", "cpu": 1.5}}


def test_process_shape_and_series():
    r = Recent()
    for ts in (1000, 1010, 1020):
        push(r, ts, {(7, 50): 1, (9, 60): ts}, meta=FAMILY)
    r._procs[(9, 60)]._cmd = ["kid", "--password=hunter2", "y" * 5000]
    d = r.process(9, 60)
    assert set(d) == {"pid", "ppid", "start_ts", "name", "app", "user", "cmdline", "alive",
                      "parent", "children", "series"}
    assert (d["pid"], d["ppid"], d["start_ts"], d["name"], d["user"]) == (9, 7, 60, "kid", "me")
    assert "hunter2" not in d["cmdline"] and len(d["cmdline"]) == 2000
    assert d["alive"] is True and d["parent"] == {"pid": 7, "name": "daddy"}
    assert d["children"] == []
    assert d["series"] == [{"ts": t, "rss": t, "cpu": 1.5} for t in (1000, 1010, 1020)]
    assert r.process(7, 50)["children"] == [{"pid": 9, "start_ts": 60, "name": "kid"}]
    assert r.process(7, 50)["parent"] is None  # pid 1 not in buffer


def test_process_exited():
    r = Recent()
    push(r, 1000, {(7, 50): 1, (9, 60): 5}, meta=FAMILY)
    push(r, 1010, {(7, 50): 1}, meta=FAMILY)
    d = r.process(9, 60)
    assert d["alive"] is False and d["series"] == [{"ts": 1000, "rss": 5, "cpu": 1.5}]
    assert r.process(7, 50)["children"] == []  # children: alive only
    push(r, 1010 + 1801, {(7, 50): 1})
    assert r.process(9, 60) is None  # aged out


def test_process_parent_pid_reuse():
    r = Recent()
    meta = {(7, 50): {"name": "real"}, (7, 500): {"name": "reused"},
            (9, 100): {"ppid": 7, "name": "a"}, (10, 600): {"ppid": 7, "name": "b"},
            (11, 40): {"ppid": 7, "name": "orphan"}}
    push(r, 1000, {k: 1 for k in meta}, meta=meta)
    assert r.process(9, 100)["parent"] == {"pid": 7, "name": "real"}
    assert r.process(10, 600)["parent"] == {"pid": 7, "name": "reused"}
    assert r.process(11, 40)["parent"] is None  # both pid-7s started after it
    assert r.process(7, 500)["children"] == [{"pid": 10, "start_ts": 600, "name": "b"}]


def test_process_unknown_none():
    r = Recent()
    assert r.process(1, 1) is None
    push(r, 1000, {(1, 1): 5})
    assert r.process(1, 2) is None and r.process(2, 1) is None


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


def test_meta_pruned_with_entries():
    r = Recent()
    push(r, 0, {(1, 1): 5, (2, 2): 5})
    push(r, 5, {(2, 2): 5})
    push(r, 100, {(2, 2): 5})  # ts 0 thinned (same 20 s bucket as 5): (1, 1) in no entry
    assert set(r.meta) == set(r._procs) == {(2, 2)}
    assert r.process(1, 1) is None
