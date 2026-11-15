from rms.store import OTHER_SMALL, Store


def mk(tmp_path):
    return Store(str(tmp_path / "sub" / "rms.db"))


def sysrow(s, ts, used=0):
    s.write_system(ts, 1000, used, 1000 - used, None, None, 0, None)


def test_query_empty_db(tmp_path):
    s = mk(tmp_path)
    pts = s.query_system(0, 100, 10)
    assert len(pts) == 10
    assert all(v is None for p in pts for k, v in p.items() if k != "ts")
    assert s.query_apps(0, 100, 10) == {}
    assert s.stats()["first_ts"] is None


def test_write_and_query_system_bucketed(tmp_path):
    s = mk(tmp_path)
    for ts, u in [(0, 100), (10, 200), (20, 300), (30, 400)]:
        sysrow(s, ts, u)
    pts = s.query_system(0, 40, 20)
    assert [(p["ts"], p["used"], p["used_max"]) for p in pts] == [(0, 150, 200), (20, 350, 400)]


def test_query_system_gap_nulls(tmp_path):
    s = mk(tmp_path)
    for ts in (0, 10, 50, 60):
        sysrow(s, ts, 100)
    pts = s.query_system(0, 70, 20)
    assert pts[1]["ts"] == 20 and pts[1]["used"] is None


def test_query_apps_missing_counts_zero(tmp_path):
    s = mk(tmp_path)
    sysrow(s, 0)
    sysrow(s, 10)
    s.write_apps(0, [("A", 100, 2)])
    b = s.query_apps(0, 20, 20)["A"][0]
    assert b["rss"] == 50 and b["rss_max"] == 100


def test_query_apps_top_and_other(tmp_path):
    s = mk(tmp_path)
    sysrow(s, 0)
    s.write_apps(0, [("a", 300, 1), ("b", 200, 1), ("c", 100, 1), (OTHER_SMALL, 5, 9)])
    assert set(s.query_apps(0, 10, 10, top=2)) == {"a", "b", OTHER_SMALL}
    assert len(s.query_apps(0, 10, 10, top=None)) == 4


def test_pick_step():
    from rms.store import pick_step
    assert pick_step(0, 3600, 10) == 10
    assert pick_step(0, 7 * 86400, 10) == 610
    assert pick_step(0, 12345, 10) % 10 == 0


def test_prune(tmp_path):
    s = mk(tmp_path)
    for ts in (0, 100):
        sysrow(s, ts)
        s.write_apps(ts, [("A", 1, 1)])
    assert s.prune(50) == 2
    st = s.stats()
    assert (st["system_rows"], st["app_rows"], st["first_ts"]) == (1, 1, 100)


def test_export_columnar_aligned(tmp_path):
    s = mk(tmp_path)
    for ts in (0, 10, 20):
        sysrow(s, ts, 5)
    s.write_apps(0, [("A", 1, 1)])
    s.write_apps(20, [("A", 3, 2)])
    d = s.export(None, None)
    n = len(d["system"]["ts"])
    assert n == 3 and d["meta"]["mode"] == "export" and d["meta"]["relative"] is False
    assert all(len(v) == n for v in d["system"].values())
    assert d["apps"]["A"]["rss"] == [1, None, 3]
    assert d["apps"]["A"]["procs"] == [1, None, 2]


def test_close_idempotent(tmp_path):
    s = mk(tmp_path)
    s.close()
    s.close()
