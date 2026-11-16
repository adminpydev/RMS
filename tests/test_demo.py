import importlib.util
import json
import re
from pathlib import Path

from rms import __version__

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("make_demo", ROOT / "scripts" / "make_demo.py")
make_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_demo)

MIB, GIB = 2**20, 2**30
N, STEP = 2160, 10
T0 = 14400
I0 = T0 // STEP  # replay start index
M = 60 // STEP  # samples per minute
D = make_demo.generate(42)


def _by_name(name):
    (p,) = [p for p in D["processes"].values() if p["name"] == name]
    return p


def _at(arr, t):
    return arr[t // STEP]


# --- insight definitions (spec 5.4), evaluated on the raw 10 s samples at index i ---
def _r2(points):
    n = len(points)
    if n < 20:
        return None
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    sxy = sum((x - mx) * (y - my) for x, y in points)
    sst = sum((y - my) ** 2 for _, y in points)
    if not sst or not sxx:
        return None
    ssr = sum((y - my - sxy / sxx * (x - mx)) ** 2 for x, y in points)
    return 1 - ssr / sst


def _trend(rss, i):
    """Last 1800 s, newest entry per 60 s bucket (aligned to now)."""
    return _r2([(j * STEP, rss[j]) for j in range(i, i - 1800 // STEP - 1, -M)
                if j >= 0 and rss[j] is not None])


def _growth(rss, i, back):
    # grid is exact, so the closest entry to now-back is the sample at i-back/STEP
    j = i - back // STEP
    return None if j < 0 or rss[i] is None or rss[j] is None else rss[i] - rss[j]


def _insights(p, i):
    rss, out = p["rss"], set()
    if rss[i] is None:
        return out
    g5, g30 = _growth(rss, i, 300), _growth(rss, i, 1800)
    if g5 is not None and g5 > 250 * MIB:
        out.add("fast")
    elif g30 is not None and g30 > 200 * MIB and (_trend(rss, i) or 0) > 0.8:
        out.add("steady")
    if p["start_ts"] >= i * STEP - 600 and rss[i] > 500 * MIB:
        out.add("newcomer")
    return out


# --- shape ---
def test_deterministic():
    assert make_demo.generate(42) == D


def test_shape_aligned_ints():
    m = D["meta"]
    assert m == {"mode": "demo", "host": "demo-mac", "os": "darwin", "total_ram": 16 * GIB,
                 "interval": 10, "first_ts": 0, "last_ts": 21590, "version": __version__,
                 "relative": True, "t0": T0}
    assert set(D) == {"meta", "system", "processes"}
    s = D["system"]
    assert set(s) == {"ts", "used", "available", "swap_used", "pressure"}
    assert s["ts"] == list(range(0, 21600, 10))
    assert all(len(v) == N and all(type(x) is int for x in v) for v in s.values())
    for i in range(N):
        assert s["available"][i] == 16 * GIB - s["used"][i] and 0 < s["used"][i] < 16 * GIB
    ps = D["processes"]
    assert 20 <= len(ps) <= 30 and set(ps) == {f"p{i}" for i in range(1, len(ps) + 1)}
    keys = {"pid", "ppid", "name", "app", "user", "cmdline", "start_ts", "rss", "cpu", "threads"}
    pids = {p["pid"] for p in ps.values()}
    assert len(pids) == len(ps)
    for p in ps.values():
        assert set(p) == keys
        cols = (p["rss"], p["cpu"], p["threads"])
        assert all(len(c) == N for c in cols)
        assert all((r is None) == (c is None) == (t is None) for r, c, t in zip(*cols))
        assert all(type(v) is int for c in cols for v in c if v is not None)
        first = next(i for i, v in enumerate(p["rss"]) if v is not None)
        assert p["start_ts"] == s["ts"][first]
        assert p["ppid"] in pids or p["pid"] in (0, 1)
    by = {p["name"]: p for p in ps.values()}
    assert by["kernel_task"]["pid"] == 0 and by["launchd"]["pid"] == 1
    assert by["WindowServer"]["user"] == "_windowserver"
    assert by["qemu-system-aarch64"]["ppid"] == by["com.docker.backend"]["pid"]


def test_committed_file_matches():
    with open(ROOT / "site" / "sample.json") as f:
        assert json.load(f) == D


def test_committed_file_size():
    assert (ROOT / "site" / "sample.json").stat().st_size <= 1.5e6


# --- planted events (spec 5.6, offsets from T0) ---
def test_steady_grower():
    rss = _by_name("Notion Helper (Renderer)")["rss"]
    assert 450 * MIB <= _at(rss, T0 + 120) - _at(rss, T0 - 28 * 60) <= 550 * MIB
    for t in (T0, T0 + 120):
        assert _trend(rss, t // STEP) > 0.9
    assert _insights(_by_name("Notion Helper (Renderer)"), I0) == {"steady"}


def test_fast_grower():
    p = _by_name("Slack Helper (Renderer)")
    assert 350 * MIB <= _at(p["rss"], T0 + 30) - _at(p["rss"], T0 - 270) <= 450 * MIB
    assert _insights(p, I0) == {"fast"}


def test_newcomer():
    p = _by_name("python3")
    assert p["start_ts"] == T0 + 60
    assert all(v is None for v in p["rss"][:I0 + M])
    assert all(v > 500 * MIB for v in p["rss"][I0 + M:])
    assert _insights(p, I0 + M) == {"newcomer"}


def test_swap_growth():
    sw = D["system"]["swap_used"]
    assert 700 * MIB <= _at(sw, T0 + 30 * 60) - _at(sw, T0 + 20 * 60) <= 900 * MIB


def _sys_insights(i):
    s, out = D["system"], set()
    if s["pressure"][i] >= 2 or s["used"][i] / (16 * GIB) > 0.90:
        out.add("pressure")
    if i >= 60 and s["swap_used"][i] - s["swap_used"][i - 60] > 500 * MIB:
        out.add("swap")
    return out


def test_pressure_window_only():
    p = D["system"]["pressure"]
    hot = [i * STEP for i in range(N) if "pressure" in _sys_insights(i)]
    assert hot and min(hot) >= T0 + 40 * 60 and max(hot) < T0 + 50 * 60
    assert all(p[i] >= 2 for i in range((T0 + 40 * 60) // STEP, (T0 + 50 * 60) // STEP))


def test_no_unredacted_secrets():
    bad = re.compile(r"--api-key [^*]|sk-demo|password=[^*]|://[^*/]+:[^*/]+@")
    cmds = [p["cmdline"] for p in D["processes"].values() if p["cmdline"]]
    assert not [c for c in cmds if bad.search(c)]
    assert any("--api-key ***" in c for c in cmds)
    assert "sk-demo" in (ROOT / "scripts" / "make_demo.py").read_text()  # raw in source


def test_quiet_otherwise():
    """Over the replay range only the planted events fire (guards the JS insight tests)."""
    allowed = {  # (name, kind): [lo, hi) sim ts
        ("Notion Helper (Renderer)", "steady"): (T0, T0 + 30 * 60),
        ("Slack Helper (Renderer)", "fast"): (T0, T0 + 5 * 60),
        ("python3", "newcomer"): (T0 + 60, T0 + 60 + 601),
        ("system", "swap"): (T0 + 20 * 60, T0 + 40 * 60),
        ("system", "pressure"): (T0 + 40 * 60, T0 + 50 * 60),
    }
    seen, bad = set(), []
    for i in range(I0, N):
        t = i * STEP
        fired = [("system", k) for k in _sys_insights(i)]
        fired += [(p["name"], k) for p in D["processes"].values() for k in _insights(p, i)]
        for f in fired:
            lo, hi = allowed.get(f, (0, 0))
            (seen.add(f) if lo <= t < hi else bad.append((t, f)))
    assert not bad, bad[:5]
    assert seen == set(allowed)
