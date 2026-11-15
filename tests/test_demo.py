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
