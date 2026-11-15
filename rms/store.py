import os
import sqlite3
import threading

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
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)

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

    def prune(self, before_ts):
        with self._lock, self._db:
            n = self._db.execute("DELETE FROM system WHERE ts < ?", (before_ts,)).rowcount
            n += self._db.execute("DELETE FROM app WHERE ts < ?", (before_ts,)).rowcount
        return n

    def stats(self):
        (sr, first, last), = self._q("SELECT COUNT(*), MIN(ts), MAX(ts) FROM system")
        (ar,), = self._q("SELECT COUNT(*) FROM app")
        try:
            size = os.path.getsize(self.path)
        except OSError:
            size = 0
        return {"path": self.path, "size_bytes": size, "system_rows": sr,
                "app_rows": ar, "first_ts": first, "last_ts": last}

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
        }
