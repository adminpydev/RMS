import importlib.util
import json
import re
from pathlib import Path

from rms import __version__

ROOT = Path(__file__).resolve().parent.parent
SAMPLE = ROOT / "site" / "sample.json"
spec = importlib.util.spec_from_file_location("make_demo", ROOT / "scripts" / "make_demo.py")
make_demo = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_demo)

GIB = 2**30
TOTAL = 16 * GIB
D = make_demo.generate(42)
SNAPS = D["snapshots"]

LIVE_KEYS = {"ts", "simulated", "system", "apps", "processes", "details"}
SYS_KEYS = {"total", "used", "available", "swap_total", "swap_used", "pressure", "breakdown"}
ROW_KEYS = {"pid", "ppid", "start_ts", "name", "app", "user", "cmdline", "rss", "share", "cpu",
            "threads", "status"}
DETAIL_KEYS = {"pid", "ppid", "start_ts", "name", "app", "user", "cmdline", "exe", "cwd", "status",
               "rss", "uss", "vms", "pfaults", "pageins", "cpu_user", "cpu_system", "threads",
               "open_files", "ports", "parent", "children", "restricted"}


def insights(live):
    """Spec section 7, in order, capped at 4. Returns [(kind, subject)]."""
    s, procs, apps = live["system"], live["processes"], live["apps"]
    out = []
    if (s["pressure"] or 0) >= 2 or s["used"] / s["total"] > 0.90:
        out.append(("pressure", None))
    if s["swap_used"] > GIB:
        out.append(("swap", None))
    big = sorted((p for p in procs if p["share"] is not None and p["share"] > 0.20),
                 key=lambda p: -p["share"])
    out += [("bigproc", p["pid"]) for p in big]
    big_apps = {p["app"] for p in big}
    out += [("bigapp", a["app"]) for a in apps if a["share"] > 0.30 and a["app"] not in big_apps]
    out += [("manyprocs", a["app"]) for a in apps if a["procs"] > 30]
    if any(p["status"] == "zombie" for p in procs):
        out.append(("zombie", None))
    return out[:4]


def _numbers(o):
    if isinstance(o, dict):
        for k, v in o.items():
            if k == "share":
                assert v is None or isinstance(v, float) and round(v, 4) == v
            else:
                yield from _numbers(v)
    elif isinstance(o, list):
        for v in o:
            yield from _numbers(v)
    elif isinstance(o, (int, float)) and not isinstance(o, bool):
        yield o


def test_deterministic():
    assert make_demo.generate(42) == D


def test_meta_and_shape():
    assert D["meta"] == {"mode": "demo", "host": "demo-mac", "os": "darwin", "total_ram": TOTAL,
                         "version": __version__}
    assert set(D) == {"meta", "snapshots"} and len(SNAPS) == 5
    for s in SNAPS:
        assert set(s) == LIVE_KEYS and s["simulated"] is True and type(s["ts"]) is int
        assert set(s["system"]) == SYS_KEYS and s["system"]["total"] == TOTAL
        assert 50 <= len(s["processes"]) <= 90
        assert all(set(p) == ROW_KEYS for p in s["processes"])
        assert all(set(a) == {"app", "rss", "share", "procs"} for a in s["apps"])
        keys = {f"{p['pid']}:{p['start_ts']}" for p in s["processes"]}
        assert len(keys) == len(s["processes"]) and set(s["details"]) == keys
        for k, d in s["details"].items():
            assert set(d) == DETAIL_KEYS and k == f"{d['pid']}:{d['start_ts']}"


def test_integers_only_except_share():
    assert all(type(n) is int for n in _numbers(D))


def test_breakdown_sums_to_total():
    for s in SNAPS:
        sy = s["system"]
        assert set(sy["breakdown"]) == {"wired", "active", "inactive", "compressed", "free"}
        assert sum(sy["breakdown"].values()) == TOTAL and min(sy["breakdown"].values()) >= 0
        b = sy["breakdown"]
        assert sy["used"] == b["wired"] + b["active"] + b["compressed"]
        assert sy["used"] + sy["available"] <= TOTAL and sy["swap_used"] <= sy["swap_total"]


def test_shares_and_apps_consistent():
    for s in SNAPS:
        procs = s["processes"]
        assert procs == sorted(procs, key=lambda p: (p["rss"] is None, -(p["rss"] or 0)))
        agg = {}
        for p in procs:
            assert p["share"] == (None if p["rss"] is None else round(p["rss"] / TOTAL, 4))
            a = agg.setdefault(p["app"], [0, 0])
            a[0] += p["rss"] or 0
            a[1] += 1
        assert {a["app"]: [a["rss"], a["procs"]] for a in s["apps"]} == agg
        assert all(a["share"] == round(a["rss"] / TOTAL, 4) for a in s["apps"])
        assert [a["rss"] for a in s["apps"]] == sorted((a["rss"] for a in s["apps"]), reverse=True)


def test_details_match_rows_and_tree():
    for s in SNAPS:
        rows = {p["pid"]: p for p in s["processes"]}
        for p in s["processes"]:
            d = s["details"][f"{p['pid']}:{p['start_ts']}"]
            for f in ("ppid", "name", "app", "user", "cmdline", "rss", "threads", "status"):
                assert d[f] == p[f], (p["pid"], f)
            par = rows.get(p["ppid"])
            if par and p["ppid"] != p["pid"]:
                assert d["parent"] == {"pid": par["pid"], "start_ts": par["start_ts"],
                                       "name": par["name"]}
                assert par["start_ts"] <= p["start_ts"]
            else:
                assert d["parent"] is None
            assert d["children"] == [{"pid": c["pid"], "start_ts": c["start_ts"], "name": c["name"]}
                                     for c in sorted(rows.values(), key=lambda c: c["pid"])
                                     if c["ppid"] == p["pid"] and c["pid"] != p["pid"]]


def test_trees_and_stable_identity():
    s1 = {p["pid"]: p for p in SNAPS[0]["processes"]}
    assert s1[0]["name"] == "kernel_task" and s1[1]["name"] == "launchd"
    assert s1[1288]["app"] == "Docker" and s1[s1[1288]["ppid"]]["name"] == "com.docker.backend"
    for s in SNAPS[1:]:
        cur = {p["pid"]: p["start_ts"] for p in s["processes"]}
        assert sum(cur.get(pid) == p["start_ts"] for pid, p in s1.items()) >= 0.9 * len(s1)


def test_restricted_and_denied_examples():
    rows = [p for s in SNAPS for p in s["processes"]]
    dets = [d for s in SNAPS for d in s["details"].values()]
    root = [d for d in dets if d["user"] == "root" and d["restricted"]]
    assert root and all(d[f] is None for d in root for f in ("uss", "cwd", "open_files", "ports"))
    assert any(p["rss"] is None and p["share"] is None and p["status"] != "zombie" for p in rows)
    assert any(p["cpu"] is None for p in rows)  # first sight / denied
    assert any(d["ports"] for d in dets) and not any(d["restricted"] for d in dets
                                                    if d["user"] == "demo"
                                                    and d["status"] != "zombie")


def test_insights_per_snapshot():
    got = [insights(s) for s in SNAPS]
    assert got[0] == []
    assert got[1] == [("bigproc", 1288)]
    assert got[2] == [("bigapp", "Google Chrome"), ("manyprocs", "Google Chrome")]
    assert got[3] == [("pressure", None), ("swap", None)]
    assert got[4] == [("zombie", None)]
    assert sum(p["status"] == "zombie" for p in SNAPS[4]["processes"]) == 2


def test_no_unredacted_secrets():
    bad = re.compile(r"--api-key [^*]|sk-demo|password=[^*]|://[^*/]+:[^*/]+@")
    text = json.dumps(D)
    assert not bad.search(text)
    assert "--api-key ***" in text
    assert "sk-demo" in (ROOT / "scripts" / "make_demo.py").read_text()  # raw in source


def test_committed_file_matches_and_size():
    with open(SAMPLE) as f:
        assert json.load(f) == D
    assert SAMPLE.stat().st_size <= 600_000
