import sqlite3

from rms.store import Store

V01 = """
CREATE TABLE system (ts INTEGER PRIMARY KEY, total INTEGER, used INTEGER, available INTEGER,
  cached INTEGER, compressed INTEGER, swap_used INTEGER, pressure INTEGER);
CREATE TABLE app (ts INTEGER, name TEXT, rss INTEGER, procs INTEGER, PRIMARY KEY (ts, name)) WITHOUT ROWID;
"""


def mk(tmp_path):
    return Store(str(tmp_path / "r.db"))


def sysrows(s, *tss):
    for t in tss:
        s.write_system(t, 100, 50, 50, 0, 0, 0, 1)


def add(s, pid, start=1, ppid=1, name="p", app="A", now=0, cmd="x"):
    return s.add_process(pid, ppid, start, name, app, "u", cmd, now)


def test_upgrade_from_v01_db(tmp_path):
    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.executescript(V01)
    c.execute("INSERT INTO system VALUES (0,100,50,50,0,0,0,1)")
    c.commit()
    c.close()
    s = Store(p)
    assert s.query_system(0, 10, 10)[0]["used"] == 50
    assert s._q("SELECT COUNT(*) FROM process") == [(0,)]


def test_add_process_idempotent(tmp_path):
    s = mk(tmp_path)
    a = add(s, 5, now=1)
    b = add(s, 5, now=9)
    assert a == b
    assert s._q("SELECT last_seen FROM process WHERE id=?", (a,)) == [(9,)]


def test_pid_reuse_new_row(tmp_path):
    s = mk(tmp_path)
    assert add(s, 5, start=1) != add(s, 5, start=2)


def test_cmdline_redacted_at_storage(tmp_path):
    s = mk(tmp_path)
    a = add(s, 1, cmd="run --password=x")
    b = add(s, 2, cmd="a" * 5000)
    assert "--password=***" in s._q("SELECT cmdline FROM process WHERE id=?", (a,))[0][0]
    assert "=x" not in s._q("SELECT cmdline FROM process WHERE id=?", (a,))[0][0]
    assert len(s._q("SELECT cmdline FROM process WHERE id=?", (b,))[0][0]) <= 2000


def test_query_processes_bucketing(tmp_path):
    s = mk(tmp_path)
    sysrows(s, 0, 10, 30, 40)
    a = add(s, 1, app="A")
    b = add(s, 2, app="B")
    s.write_process_samples(0, [(a, 100, 40, 1), (b, 10, 5, 1)])
    s.write_process_samples(30, [(a, 300, 60, 1)])
    r = s.query_processes(0, 50, 20)
    assert set(r) == {str(a), str(b)}
    ser = r[str(a)]["series"]
    assert ser[0] == {"ts": 0, "rss": 50, "rss_max": 100, "cpu": 40}
    assert ser[1] == {"ts": 20, "rss": 300, "rss_max": 300, "cpu": 60}
    assert ser[2] == {"ts": 40, "rss": 0, "rss_max": 0, "cpu": None}
    gap = s.query_processes(0, 70, 10)[str(a)]["series"]
    assert gap[2] == {"ts": 20, "rss": None, "rss_max": None, "cpu": None}
    assert set(s.query_processes(0, 50, 20, app="B")) == {str(b)}
    assert list(s.query_processes(0, 50, 20, top=1)) == [str(a)]


def test_get_process_parent_children(tmp_path):
    s = mk(tmp_path)
    p = add(s, 1, ppid=0, name="par", now=100)
    c = add(s, 2, ppid=1, name="kid", now=100)
    o = add(s, 3, ppid=99, now=100)
    d = s.get_process(c, 0, 200, 10)
    assert d["parent"] == {"id": p, "pid": 1, "name": "par"}
    assert d["series"] and d["first_seen"] == 100
    assert s.get_process(p, 0, 200, 10)["children"] == [{"id": c, "pid": 2, "name": "kid"}]
    assert s.get_process(o, 0, 200, 10)["parent"] == {"id": None, "pid": 99, "name": None}
    assert s.get_process(9999, 0, 200, 10) is None


def test_prune_processes(tmp_path):
    s = mk(tmp_path)
    old = add(s, 1, now=5)
    live = add(s, 2, now=500)
    s.write_process_samples(5, [(old, 1, 1, 1), (live, 1, 1, 1)])
    s.write_process_samples(500, [(live, 1, 1, 1)])
    n = s.prune(100)
    assert n == 3
    assert s._q("SELECT ts FROM process_sample WHERE proc=?", (live,)) == [(500,)]
    assert s.last_pruned_ids == [old]
    assert s._q("SELECT id FROM process") == [(live,)]
    s.prune(100)
    assert s.last_pruned_ids == []


def test_export_processes_aligned(tmp_path):
    s = mk(tmp_path)
    sysrows(s, 0, 10, 20)
    a = add(s, 1, name="n", app="A")
    s.write_process_samples(10, [(a, 7, 3, 1)])
    e = s.export(None, None)
    assert e["processes"][str(a)]["rss"] == [None, 7, None]
    assert e["processes"][str(a)]["cpu"] == [None, 3, None]
    assert e["processes"][str(a)]["name"] == "n"
    st = s.stats()
    assert st["process_rows"] == 1 and st["process_sample_rows"] == 1


def test_parent_respects_pid_reuse(tmp_path):
    s = mk(tmp_path)
    old = add(s, 1, start=10, ppid=0, name="old", now=100)
    new = add(s, 1, start=300, ppid=0, name="new", now=400)
    c1 = add(s, 2, start=50, ppid=1, now=100)
    c2 = add(s, 3, start=350, ppid=1, now=400)
    assert s.get_process(c1, 0, 500, 10)["parent"]["id"] == old
    assert s.get_process(c2, 0, 500, 10)["parent"]["id"] == new
    assert [c["id"] for c in s.get_process(old, 0, 500, 10)["children"]] == [c1, c2]
    assert [c["id"] for c in s.get_process(new, 0, 500, 10)["children"]] == [c2]


def test_add_processes_batch(tmp_path):
    s = mk(tmp_path)
    ids = s.add_processes([(1, 0, 1, "a", "A", "u", "--password=x"), (2, 0, 1, "b", "A", None, None)], 5)
    assert len(set(ids)) == 2
    assert s.add_processes([(1, 0, 1, "a", "A", "u", "z")], 9) == [ids[0]]


def test_touch_processes(tmp_path):
    s = mk(tmp_path)
    ids = s.add_processes([(i, 0, 1, "n", "A", "u", "c") for i in range(1200)], 5)
    s.touch_processes(77, ids[:1])
    assert s._q("SELECT COUNT(*) FROM process WHERE last_seen=77") == [(1,)]
    s.touch_processes(88, ids)
    assert s._q("SELECT COUNT(*) FROM process WHERE last_seen=88") == [(1200,)]
