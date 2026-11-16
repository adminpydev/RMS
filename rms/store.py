import os
import sqlite3
import threading

from .redact import redact

OTHER_SMALL = "Other (small apps)"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS system (
  ts INTEGER PRIMARY KEY,
  total INTEGER, used INTEGER, available INTEGER,
  cached INTEGER, compressed INTEGER, swap_used INTEGER,
  pressure INTEGER
);
CREATE TABLE IF NOT EXISTS app (
  ts INTEGER, name TEXT, rss INTEGER, procs INTEGER,
  PRIMARY KEY (ts, name)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS app_name_ts ON app(name, ts);
CREATE TABLE IF NOT EXISTS process (
  id INTEGER PRIMARY KEY,
  pid INTEGER NOT NULL, ppid INTEGER, start_ts INTEGER NOT NULL,
  name TEXT NOT NULL, app TEXT NOT NULL,
  user TEXT,
  cmdline TEXT,
  first_seen INTEGER NOT NULL, last_seen INTEGER NOT NULL,
  UNIQUE (pid, start_ts)
);
CREATE TABLE IF NOT EXISTS process_sample (
  ts INTEGER NOT NULL, proc INTEGER NOT NULL,
  rss INTEGER NOT NULL, cpu INTEGER,
  threads INTEGER,
  PRIMARY KEY (ts, proc)
) WITHOUT ROWID;
CREATE INDEX IF NOT EXISTS process_sample_proc_ts ON process_sample(proc, ts);
CREATE INDEX IF NOT EXISTS process_app ON process(app);
"""

_SYS_COLS = ("used", "available", "cached", "compressed", "swap_used", "pressure")


def pick_step(frm, to, interval, max_points=1000):
    need = -(-(to - frm) // max_points)  # ceil
    return max(interval, -(-need // interval) * interval)


class Store:
    def __init__(self, path):
        self.path = path
        d = os.path.dirname(os.path.abspath(path))
        os.makedirs(d, exist_ok=True)
        self._lock = threading.Lock()
        self.last_pruned_ids = []
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

    def close(self):
        with self._lock:
            self._db.close()  # sqlite3 close is idempotent

    def _q(self, sql, args=()):
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def write_system(self, ts, total, used, available, cached, compressed, swap_used, pressure):
        with self._lock, self._db:
            self._db.execute(
                "INSERT OR REPLACE INTO system VALUES (?,?,?,?,?,?,?,?)",
                (ts, total, used, available, cached, compressed, swap_used, pressure))

    def write_apps(self, ts, rows):
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO app VALUES (?,?,?,?)",
                [(ts, n, r, p) for n, r, p in rows])

    def query_system(self, frm, to, step):
        rows = self._q(
            "SELECT ts/:s*:s, CAST(AVG(used) AS INT), CAST(AVG(available) AS INT),"
            " CAST(AVG(cached) AS INT), CAST(AVG(compressed) AS INT),"
            " CAST(AVG(swap_used) AS INT), MAX(pressure), MAX(used)"
            " FROM system WHERE ts >= :f/:s*:s AND ts < :t GROUP BY 1",
            {"s": step, "f": frm, "t": to})
        by = {r[0]: r for r in rows}
        out = []
        for b in range(frm // step * step, to, step):
            r = by.get(b)
            keys = _SYS_COLS + ("used_max",)
            out.append({"ts": b, **dict(zip(keys, r[1:] if r else [None] * len(keys)))})
        return out

    def query_apps(self, frm, to, step, top=15):
        counts = dict(self._q(
            "SELECT ts/:s*:s, COUNT(*) FROM system WHERE ts >= :f/:s*:s AND ts < :t GROUP BY 1",
            {"s": step, "f": frm, "t": to}))
        rows = self._q(
            "SELECT name, ts/:s*:s, SUM(rss), MAX(rss), SUM(procs) FROM app"
            " WHERE ts >= :f/:s*:s AND ts < :t GROUP BY 1, 2",
            {"s": step, "f": frm, "t": to})
        by, total = {}, {}
        for name, b, s, mx, pr in rows:
            if b not in counts:
                continue
            by.setdefault(name, {})[b] = (s, mx, pr)
            total[name] = total.get(name, 0) + s
        names = sorted((n for n in by if n != OTHER_SMALL), key=lambda n: -total[n])
        if top is not None:
            names = names[:top]
        if OTHER_SMALL in by:
            names.append(OTHER_SMALL)
        out = {}
        for n in names:
            series = []
            for b in range(frm // step * step, to, step):
                c = counts.get(b)
                if not c:
                    series.append({"ts": b, "rss": None, "rss_max": None, "procs": None})
                else:
                    s, mx, pr = by[n].get(b, (0, 0, 0))
                    series.append({"ts": b, "rss": s // c, "rss_max": mx, "procs": pr // c})
            out[n] = series
        return out

    def add_process(self, pid, ppid, start_ts, name, app, user, cmdline, now):
        cmd = redact(cmdline)
        if cmd is not None:
            cmd = cmd[:2000]  # truncate after redaction
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO process (pid, ppid, start_ts, name, app, user, cmdline, first_seen, last_seen)"
                " VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT(pid, start_ts) DO UPDATE SET last_seen=excluded.last_seen",
                (pid, ppid, start_ts, name, app, user, cmd, now, now))
            return self._db.execute(
                "SELECT id FROM process WHERE pid=? AND start_ts=?", (pid, start_ts)).fetchone()[0]

    def write_process_samples(self, ts, rows):
        with self._lock, self._db:
            self._db.executemany(
                "INSERT OR REPLACE INTO process_sample VALUES (?,?,?,?,?)",
                [(ts, p, r, c, t) for p, r, c, t in rows])

    def touch_processes(self, ts, ids):
        with self._lock, self._db:
            for k in range(0, len(ids), 500):
                chunk = ids[k:k + 500]
                self._db.execute(
                    "UPDATE process SET last_seen=? WHERE id IN (%s)" % ",".join("?" * len(chunk)),
                    (ts, *chunk))

    def _process_series(self, frm, to, step, rows, counts, ensure=()):
        """rows: (proc, bucket, sum_rss, max_rss, avg_cpu) -> {proc: [point...]}"""
        by = {p: {} for p in ensure}
        for p, b, s, mx, cpu in rows:
            if b in counts:
                by.setdefault(p, {})[b] = (s, mx, cpu)
        out = {}
        for p, m in by.items():
            series = []
            for b in range(frm // step * step, to, step):
                c = counts.get(b)
                if not c:
                    series.append({"ts": b, "rss": None, "rss_max": None, "cpu": None})
                else:
                    s, mx, cpu = m.get(b, (0, 0, None))
                    series.append({"ts": b, "rss": s // c, "rss_max": mx,
                                   "cpu": None if cpu is None else int(cpu)})
            out[p] = series
        return out

    def _counts(self, frm, to, step):
        return dict(self._q(
            "SELECT ts/:s*:s, COUNT(*) FROM system WHERE ts >= :f/:s*:s AND ts < :t GROUP BY 1",
            {"s": step, "f": frm, "t": to}))

    _PROC_SQL = ("SELECT proc, ts/:s*:s, SUM(rss), MAX(rss), AVG(cpu) FROM process_sample"
                 " WHERE ts >= :f/:s*:s AND ts < :t %s GROUP BY 1, 2")

    def query_processes(self, frm, to, step, app=None, top=25):
        counts = self._counts(frm, to, step)
        args = {"s": step, "f": frm, "t": to}
        extra = ""
        if app is not None:
            extra = "AND proc IN (SELECT id FROM process WHERE app = :app)"
            args["app"] = app
        rows = self._q(self._PROC_SQL % extra, args)
        series = self._process_series(frm, to, step, rows, counts)
        total = {}
        for p, b, s, _, _ in rows:
            if b in counts:
                total[p] = total.get(p, 0) + s
        ids = sorted(series, key=lambda p: -total[p])
        if top is not None:
            ids = ids[:top]
        out = {}
        for p in ids:
            r = self._q("SELECT pid, ppid, name, app, user, cmdline, start_ts FROM process"
                        " WHERE id = ?", (p,))
            if r:
                meta = dict(zip(("pid", "ppid", "name", "app", "user", "cmdline", "start_ts"), r[0]))
                out[str(p)] = {**meta, "series": series[p]}
        return out

    def get_process(self, id, frm, to, step):
        r = self._q("SELECT pid, ppid, name, app, user, cmdline, start_ts, first_seen, last_seen"
                    " FROM process WHERE id = ?", (id,))
        if not r:
            return None
        keys = ("pid", "ppid", "name", "app", "user", "cmdline", "start_ts", "first_seen", "last_seen")
        d = dict(zip(keys, r[0]))
        ov = "first_seen < ? AND last_seen >= ?"
        parent = None
        if d["ppid"] is not None:
            pr = self._q("SELECT id, pid, name FROM process WHERE pid = ? AND id != ? AND " + ov +
                         " ORDER BY last_seen DESC LIMIT 1", (d["ppid"], id, to, frm))
            parent = ({"id": pr[0][0], "pid": pr[0][1], "name": pr[0][2]} if pr
                      else {"id": None, "pid": d["ppid"], "name": None})
        children = [{"id": i, "pid": p, "name": n} for i, p, n in self._q(
            "SELECT id, pid, name FROM process WHERE ppid = ? AND id != ? AND " + ov + " ORDER BY id",
            (d["pid"], id, to, frm))]
        rows = self._q(self._PROC_SQL % "AND proc = :p", {"s": step, "f": frm, "t": to, "p": id})
        series = self._process_series(frm, to, step, rows, self._counts(frm, to, step), (id,))
        return {"id": id, **d, "parent": parent, "children": children, "series": series[id]}

    def prune(self, before_ts):
        self.last_pruned_ids = []
        with self._lock, self._db:
            n = self._db.execute("DELETE FROM system WHERE ts < ?", (before_ts,)).rowcount
            n += self._db.execute("DELETE FROM app WHERE ts < ?", (before_ts,)).rowcount
            n += self._db.execute("DELETE FROM process_sample WHERE ts < ?", (before_ts,)).rowcount
            ids = [r[0] for r in self._db.execute(
                "SELECT id FROM process WHERE last_seen < ?", (before_ts,))]
            n += self._db.execute("DELETE FROM process WHERE last_seen < ?", (before_ts,)).rowcount
            self.last_pruned_ids = ids
        return n

    def stats(self):
        (sr, first, last), = self._q("SELECT COUNT(*), MIN(ts), MAX(ts) FROM system")
        (ar,), = self._q("SELECT COUNT(*) FROM app")
        (pr,), = self._q("SELECT COUNT(*) FROM process")
        (psr,), = self._q("SELECT COUNT(*) FROM process_sample")
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = 0
        return {"path": self.path, "size_bytes": size, "system_rows": sr,
                "app_rows": ar, "process_rows": pr, "process_sample_rows": psr,
                "first_ts": first, "last_ts": last}

    def export(self, frm, to):
        lo = frm if frm is not None else -(2 ** 62)
        hi = to if to is not None else 2 ** 62
        rows = self._q(
            "SELECT ts, total, used, available, cached, compressed, swap_used, pressure"
            " FROM system WHERE ts >= ? AND ts <= ? ORDER BY ts", (lo, hi))
        ts = [r[0] for r in rows]
        idx = {t: i for i, t in enumerate(ts)}
        apps = {}
        for name, t, rss, procs in self._q(
                "SELECT name, ts, rss, procs FROM app WHERE ts >= ? AND ts <= ? ORDER BY ts",
                (lo, hi)):
            i = idx.get(t)
            if i is None:
                continue
            a = apps.setdefault(name, {"rss": [None] * len(ts), "procs": [None] * len(ts)})
            a["rss"][i], a["procs"][i] = rss, procs
        return {
            "meta": {"mode": "export", "total_ram": rows[-1][1] if rows else None,
                     "first_ts": ts[0] if ts else None, "last_ts": ts[-1] if ts else None,
                     "relative": False},
            "system": {"ts": ts, **{k: [r[2 + j] for r in rows] for j, k in enumerate(_SYS_COLS)}},
            "apps": apps,
            "processes": self._export_processes(lo, hi, ts, idx),
        }

    def _export_processes(self, lo, hi, ts, idx):
        out = {}
        for pid_, t, rss, cpu in self._q(
                "SELECT proc, ts, rss, cpu FROM process_sample"
                " WHERE ts >= ? AND ts <= ? ORDER BY ts", (lo, hi)):
            i = idx.get(t)
            if i is None:
                continue
            o = out.setdefault(pid_, {"rss": [None] * len(ts), "cpu": [None] * len(ts)})
            o["rss"][i], o["cpu"][i] = rss, cpu
        res = {}
        for id_ in out:
            r = self._q("SELECT pid, ppid, name, app, user, cmdline, start_ts FROM process"
                        " WHERE id = ?", (id_,))
            if r:
                meta = dict(zip(("pid", "ppid", "name", "app", "user", "cmdline", "start_ts"), r[0]))
                res[str(id_)] = {**meta, **out[id_]}
        return res
