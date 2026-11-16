import bisect
import logging
import threading
import time

import psutil

from rms.redact import redact

_SKIP = (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess)
_BASE = ("pid", "ppid", "start_ts", "name", "app")
_SYS = ("total", "used", "available", "swap_used", "pressure")


def _read(fn):
    try:
        return fn()
    except _SKIP:
        return None


def _near(entries, target, tol):
    """Entry closest to `target`, if within `tol` s, else None."""
    e = min(entries, key=lambda e: abs(e[0] - target))
    return e if abs(e[0] - target) <= tol else None


def _r2(points):
    """R² of the least-squares line through (ts, rss) points; None if < 20 points or flat."""
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
    slope = sxy / sxx
    ssr = sum((y - my - slope * (x - mx)) ** 2 for x, y in points)
    return 1 - ssr / sst


class Recent:
    """Last `window` s of every process: entries (ts, system, {key: (rss, cpu, threads)})."""

    def __init__(self, window=1800):
        self.window = window
        self.entries = []
        self.meta = {}     # key -> {pid, ppid, start_ts, name, app[, user, cmdline]}
        self._procs = {}   # key -> psutil.Process, for lazy user/cmdline reads
        self._lock = threading.Lock()

    def push(self, ts, system, procs_full):
        procs = {k: (v["rss"], v["cpu"], v["threads"]) for k, v in procs_full.items()}
        with self._lock:
            # by ts: a sampler push can land before an older collector push
            # ponytail: O(n) key list (n ~80 after thinning); insort(key=) needs 3.10
            i = bisect.bisect_right([e[0] for e in self.entries], ts)
            self.entries.insert(i, (ts, {k: system.get(k) for k in _SYS}, procs))
            for k, v in procs_full.items():
                if k not in self.meta:
                    self.meta[k] = {f: v[f] for f in _BASE}
                    self._procs[k] = v["proc"]
            now = self.entries[-1][0]
            cut = now - self.window
            # every entry for the last 60 s; newest per 20 s bucket to 10 min; per 60 s beyond
            # ponytail: O(n) rebuild per push, n stays ~80
            keep, seen = [], set()
            for e in reversed(self.entries):
                if e[0] < cut:
                    break
                age = now - e[0]
                if age > 60:
                    b = (20, e[0] // 20) if age <= 600 else (60, e[0] // 60)
                    if b in seen:
                        continue
                    seen.add(b)
                keep.append(e)
            self.entries = keep[::-1]
            # meta lives exactly as long as the key is in some retained entry
            # ponytail: O(entries x procs) union per push (~80 x ~500); fine at 2 s cadence
            present = set().union(*(e[2] for e in self.entries))
            for k in [k for k in self.meta if k not in present]:
                del self.meta[k], self._procs[k]

    @staticmethod
    def _parent(meta, alive, ppid, start_ts):
        """Newest alive process `ppid` started at or before `start_ts` (PID reuse guard)."""
        cand = [meta[k] for k in alive
                if k in meta and k[0] == ppid and k[1] <= start_ts]
        return max(cand, key=lambda m: m["start_ts"]) if cand else None

    def fill_meta(self, proc):
        cmd = _read(proc.cmdline)
        return {"user": _read(proc.username),
                "cmdline": redact(" ".join(cmd))[:2000] if cmd is not None else None}  # cap 2000

    def _fill(self, keys):
        """Read user/cmdline once per key (outside the lock: slow syscalls); return meta copies."""
        with self._lock:
            todo = [(k, self._procs[k]) for k in keys
                    if k in self.meta and "cmdline" not in self.meta[k]]
        filled = {k: self.fill_meta(p) for k, p in todo}
        with self._lock:
            for k, m in filled.items():
                if k in self.meta:
                    self.meta[k].update(m)
            return {k: dict(self.meta.get(k) or {"user": None, "cmdline": None}) for k in keys}

    def snapshot(self):
        with self._lock:
            if not self.entries:
                return {"ts": None, "resolution": None, "simulated": False, "system": None,
                        "processes": []}
            entries = list(self.entries)
        ts, system, procs = entries[-1]
        meta = self._fill(list(procs))

        res = 2 if len(entries) > 1 and ts - entries[-2][0] <= 3 else 10
        old = (_near(entries, ts - 300, 30) or (0, 0, {}))[2]
        old30 = (_near(entries, ts - 1800, 90) or (0, 0, {}))[2]
        swap_ago = _near(entries, ts - 600, 60)
        growth = {k: v[0] - old[k][0] for k, v in procs.items() if k in old}
        growth30 = {k: v[0] - old30[k][0] for k, v in procs.items() if k in old30}

        top = set(sorted(procs, key=lambda k: procs[k][0], reverse=True)[:50])
        top |= set(sorted((k for k in growth if growth[k] > 0), key=growth.get, reverse=True)[:20])
        buckets, minutes = {}, {}
        for e in entries:
            b = (ts - e[0]) // 20
            if b < 30:
                buckets[b] = e[2]  # newest entry in each 20 s bucket
            minutes[(ts - e[0]) // 60] = e  # newest entry in each 60 s bucket (trend)
        spark_src = [buckets[b] for b in sorted(buckets, reverse=True)]

        def trend(k):
            if growth30.get(k, 0) <= 200 * 2**20:
                return None
            r2 = _r2([(e[0], e[2][k][0]) for e in minutes.values() if k in e[2]])
            return {"r2": r2} if r2 is not None else None

        rows = []
        for k, (rss, cpu, threads) in procs.items():
            m = meta[k]
            rows.append({**{f: m.get(f) for f in _BASE},
                         "user": m.get("user"), "cmdline": m.get("cmdline"),
                         "rss": rss, "cpu": cpu, "threads": threads,
                         "growth_5m": growth.get(k), "growth_30m": growth30.get(k),
                         "trend": trend(k),
                         "spark": [e[k][0] for e in spark_src if k in e] if k in top else None})
        return {"ts": ts, "resolution": res, "simulated": False,
                "system": {**{f: system[f] for f in ("total", "used", "available", "swap_used")},
                           "swap_used_10m_ago": swap_ago[1]["swap_used"] if swap_ago else None,
                           "pressure": system["pressure"]},
                "processes": rows}

    def process(self, pid, start_ts):
        """One process from the buffer (alive or exited), or None once it has aged out."""
        key = (pid, start_ts)
        with self._lock:
            if key not in self.meta:
                return None
            base = dict(self.meta[key])  # copy now: a concurrent push may prune it
            series = [{"ts": e[0], "rss": e[2][key][0], "cpu": e[2][key][1]}
                      for e in self.entries if key in e[2]]
            alive = self.entries[-1][2]
            par = self._parent(self.meta, alive, base["ppid"], start_ts)
            kids = sorted((k for k in alive if k != key and k in self.meta
                           and self.meta[k]["ppid"] == pid and k[1] >= start_ts),
                          key=lambda k: (k[1], k[0]))
            children = [{"pid": k[0], "start_ts": k[1], "name": self.meta[k]["name"]}
                        for k in kids]
            parent = ({"pid": par["pid"], "name": par["name"]}
                      if par and (par["pid"], par["start_ts"]) != key else None)
        if not series:
            return None
        # an exited process never filled while alive keeps user/cmdline None (intended)
        m = self._fill([key])[key]
        return {**{f: base[f] for f in _BASE}, "user": m.get("user"),
                "cmdline": m.get("cmdline"), "alive": key in alive,
                "parent": parent, "children": children, "series": series}


class LiveSampler:
    """2 s samples into the recent buffer, only while a Live tab keeps poking."""

    def __init__(self, recent, collector, period=2, idle=15, clock=time.monotonic):
        self.recent, self.collector = recent, collector
        self.period, self.idle, self.clock = period, idle, clock
        self.last_poke = float("-inf")

    def poke(self):
        self.last_poke = self.clock()

    def run(self, stop):
        while not stop.is_set():
            if self.clock() - self.last_poke < self.idle:
                try:
                    self.collector.scan_live(int(time.time()))
                except Exception:
                    logging.getLogger("rms.live").exception("live sample failed")
            stop.wait(self.period)
