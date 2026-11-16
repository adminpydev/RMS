import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_demo", ROOT / "scripts" / "make_demo.py")
make_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_demo)

MIB, GIB = 2**20, 2**30
N = 8640
H = 3600 // 30  # samples per hour


def test_deterministic():
    assert make_demo.generate(42) == make_demo.generate(42)


def test_shape_aligned():
    d = make_demo.generate(42)
    assert d["meta"]["relative"] is True and d["meta"]["mode"] == "demo"
    assert d["meta"]["total_ram"] == 16 * GIB
    assert all(len(v) == N for v in d["system"].values())
    for a in d["apps"].values():
        assert len(a["rss"]) == N and len(a["procs"]) == N
    assert "Other (small apps)" in d["apps"] and len(d["apps"]) == 13


def test_invariants():
    d = make_demo.generate(42)
    s, total = d["system"], d["meta"]["total_ram"]
    for i in range(N):
        assert s["used"][i] <= 0.95 * total
        assert s["available"][i] == total - s["used"][i]
        rss = sum(a["rss"][i] or 0 for a in d["apps"].values())
        assert rss <= s["used"][i] * 1.2
    assert set(s["pressure"]) == {1, 2} and s["cached"][0] is None


def test_planted_leak_present():
    n = make_demo.generate(42)["apps"]["Notion"]["rss"]
    day2 = 86400 // 30
    grow = (n[day2 + 16 * H - 1] - n[day2 + 10 * H]) / MIB
    assert 800 <= grow <= 1000


def test_committed_file_matches():
    with open(ROOT / "site" / "sample.json") as f:
        assert json.load(f) == make_demo.generate(42)


# --- guard: only planted events trip the Task 6 detectors (270 s buckets) ---
def _buckets(arr):
    out = []
    for i in range(0, len(arr), 9):
        v = [x for x in arr[i:i + 9] if x is not None]
        out.append(sum(v) / len(v) if v else None)
    return out


def _leaks(b, null_as_zero):
    """Least-squares leak rule over 2h/6h/24h windows."""
    pts = [(i, 0 if (v is None and null_as_zero) else v) for i, v in enumerate(b)]
    for hours, stride in ((2, 7), (6, 20), (24, 80)):
        w = round(hours * 3600 / 270)
        for s in range(0, len(b) - w + 1, stride):
            p = [(x, y / MIB) for x, y in pts[s:s + w] if y is not None]
            if len(p) < w / 2:  # detectors need >=50% coverage
                continue
            n = len(p)
            mx, my = sum(x for x, _ in p) / n, sum(y for _, y in p) / n
            sxx = sum((x - mx) ** 2 for x, _ in p)
            sxy = sum((x - mx) * (y - my) for x, y in p)
            syy = sum((y - my) ** 2 for _, y in p)
            if sxx == 0 or syy == 0:
                continue
            slope = sxy / sxx * (3600 / 270)  # MiB/h
            if slope > 50 and sxy * sxy / (sxx * syy) > 0.8 and slope * hours > 200:
                return True
    return False


def test_no_false_events():
    d = make_demo.generate(42)
    for zero in (False, True):
        flagged = {n for n, a in d["apps"].items() if _leaks(_buckets(a["rss"]), zero)}
        assert flagged == {"Notion"}, (zero, flagged)
    u = [x / GIB for x in _buckets(d["system"]["used"])]
    planted = (14 * 3600, 2 * 86400 + 11 * 3600)
    fired = {"spike": [], "rise": []}
    for i in range(len(u) - 2):
        rise = max(u[i + 1], u[i + 2]) - u[i]
        t = i * 270
        near = any(abs(t - p) <= 600 for p in planted)
        if rise > 1.6:
            fired["spike"].append(t)
            assert near, t
        elif not near:
            assert rise <= 0.8, (t, rise)
    assert len(fired["spike"]) >= 2


# --- v0.2: processes section ---
def _procs():
    return make_demo.generate(42)["processes"]


def test_processes_aligned():
    d = make_demo.generate(42)
    ps, ts = d["processes"], d["system"]["ts"]
    assert len(ps) >= 25 and set(ps) == {f"p{i}" for i in range(1, len(ps) + 1)}
    keys = {"pid", "ppid", "name", "app", "user", "cmdline", "start_ts", "rss", "cpu"}
    pids = {p["pid"] for p in ps.values()}
    assert len(pids) == len(ps)
    for p in ps.values():
        assert set(p) == keys
        assert len(p["rss"]) == N and len(p["cpu"]) == N
        assert all((r is None) == (c is None) for r, c in zip(p["rss"], p["cpu"]))
        assert all(type(v) is int for v in p["rss"] + p["cpu"] if v is not None)
        first = next(i for i, v in enumerate(p["rss"]) if v is not None)
        assert p["start_ts"] == ts[first]
        assert p["ppid"] in pids or p["pid"] in (0, 1)
    by = {p["name"]: p for p in ps.values()}
    assert by["kernel_task"]["pid"] == 0 and by["kernel_task"]["user"] == "root"
    assert by["launchd"]["pid"] == 1 and by["launchd"]["user"] == "root"
    assert by["WindowServer"]["user"] == "_windowserver"
    assert by["qemu-system-aarch64"]["ppid"] == by["com.docker.backend"]["pid"]
    assert sum(p["user"] is None for p in ps.values()) == 1


def test_app_rss_is_sum_of_processes():
    """Exact (tolerance 0): every app series is the sum of its processes.
    Processes of apps outside the 13 listed ones are folded into "Other (small apps)"."""
    d = make_demo.generate(42)
    groups = {}
    for p in d["processes"].values():
        a = p["app"] if p["app"] in d["apps"] else "Other (small apps)"
        groups.setdefault(a, []).append(p["rss"])
    assert set(groups) == set(d["apps"])
    for name, series in groups.items():
        for i, v in enumerate(d["apps"][name]["rss"]):
            vals = [s[i] for s in series if s[i] is not None]
            assert v == (sum(vals) if vals else None), (name, i)


def test_planted_process_events():
    ps = _procs()
    by = {p["name"]: p for p in ps.values()}
    day = 86400 // 30
    n = by["Notion Helper (Renderer)"]["rss"]
    assert 800 <= (n[day + 16 * H - 1] - n[day + 10 * H]) / MIB <= 1000
    q = by["qemu-system-aarch64"]["rss"]
    s = 14 * H
    assert q[s + 6] - q[s - 1] >= 2 * GIB
    r = [p for p in ps.values() if p["name"] == "Google Chrome Helper (Renderer)"]
    s = 2 * day + 11 * H
    assert sum(p["rss"][s + 6] - p["rss"][s - 1] >= 2 * GIB for p in r) == 1


def test_replay_end_content():
    """End of the Live replay: a big newcomer (start in last 600 s, > 500 MiB) that is also
    a fast grower (> 250 MiB in 5 min), plus a mid-day-3 newcomer still running > 500 MiB."""
    ps, end = _procs(), (N - 1) * 30
    assert any(p["start_ts"] >= end - 600 and p["rss"][-1] > 500 * MIB
               and p["rss"][-1] - p["rss"][-11] > 250 * MIB for p in ps.values())
    assert any(2 * 86400 + 6 * 3600 <= p["start_ts"] <= end - 6 * 3600
               and p["rss"][-1] > 500 * MIB for p in ps.values())


def test_no_unredacted_secrets():
    import re
    bad = re.compile(r"--api-key [^*]|password=[^*]|://[^*/]+:[^*/]+@")
    cmds = [p["cmdline"] for p in _procs().values() if p["cmdline"]]
    assert not [c for c in cmds if bad.search(c)]
    assert any("--api-key ***" in c for c in cmds)


def test_committed_file_size():
    assert (ROOT / "site" / "sample.json").stat().st_size <= 4.5e6
