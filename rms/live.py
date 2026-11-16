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


class Recent:
    """Last `window` s of every process: entries (ts, system, {key: (rss, cpu, threads)})."""

    def __init__(self, window=600):
        self.window = window
        self.entries = []
        self.meta = {}     # key -> {pid, ppid, start_ts, name, app[, user, cmdline]}
        self._procs = {}   # key -> psutil.Process, for lazy user/cmdline reads
        self._last = {}    # key -> ts last seen in an entry
        self._lock = threading.Lock()

    def push(self, ts, system, procs_full):
        procs = {k: (v["rss"], v["cpu"], v["threads"]) for k, v in procs_full.items()}
        with self._lock:
            self.entries.append((ts, {k: system.get(k) for k in _SYS}, procs))
            for k, v in procs_full.items():
                if k not in self.meta:
                    self.meta[k] = {f: v[f] for f in _BASE}
                    self._procs[k] = v["proc"]
                self._last[k] = ts
            cut = ts - self.window
            # ponytail: list slice, O(n) per push; deque if the window grows
            i = 0
            while i < len(self.entries) and self.entries[i][0] < cut:
                i += 1
            del self.entries[:i]
            for k in [k for k, t in self._last.items() if t < cut]:
                del self._last[k], self.meta[k], self._procs[k]

    def name_of(self, pid, before_start_ts):
        """Name of the live process `pid` (newest buffer entry) started at or before the cutoff."""
        with self._lock:
            if not self.entries:
                return None
            cand = [self.meta[k] for k in self.entries[-1][2]
                    if k in self.meta and self.meta[k]["pid"] == pid
                    and self.meta[k]["start_ts"] <= before_start_ts]
        return max(cand, key=lambda m: m["start_ts"])["name"] if cand else None

    def fill_meta(self, key, proc):
        cmd = _read(proc.cmdline)
        return {"user": _read(proc.username),
                "cmdline": redact(" ".join(cmd)) if cmd is not None else None}

    def snapshot(self, ids):
        with self._lock:
            if not self.entries:
                return {"ts": None, "resolution": 10, "simulated": False, "system": None,
                        "processes": []}
            entries = list(self.entries)
            ts, system, procs = entries[-1]
            todo = [(k, self._procs[k]) for k in procs if "cmdline" not in self.meta[k]]
        filled = {k: self.fill_meta(k, p) for k, p in todo}  # outside the lock: slow syscalls
        with self._lock:
            for k, m in filled.items():
                if k in self.meta:
                    self.meta[k].update(m)
            meta = {k: dict(self.meta.get(k) or {"user": None, "cmdline": None}) for k in procs}

        res = 2 if len(entries) > 1 and ts - entries[-2][0] <= 3 else 10
        near = min(entries, key=lambda e: abs(e[0] - (ts - 300)))
        old = near[2] if abs(near[0] - (ts - 300)) <= 30 else {}
        swap_ago = entries[0][1]["swap_used"] if ts - entries[0][0] >= 540 else None
        growth = {k: v[0] - old[k][0] for k, v in procs.items() if k in old}

        top = set(sorted(procs, key=lambda k: procs[k][0], reverse=True)[:50])
        top |= set(sorted((k for k in growth if growth[k] > 0), key=growth.get, reverse=True)[:20])
        buckets = {}
        for e in entries:
            b = (ts - e[0]) // 20
            if b < 30:
                buckets[b] = e[2]  # newest entry in each 20 s bucket
        spark_src = [buckets[b] for b in sorted(buckets, reverse=True)]

        rows = []
        for k, (rss, cpu, threads) in procs.items():
            m = meta[k]
            rows.append({"id": ids.get(k), **{f: m.get(f) for f in _BASE},
                         "user": m.get("user"), "cmdline": m.get("cmdline"),
                         "rss": rss, "cpu": cpu, "threads": threads,
                         "growth_5m": growth.get(k),
                         "spark": [e[k][0] for e in spark_src if k in e] if k in top else None})
        return {"ts": ts, "resolution": res, "simulated": False,
                "system": {**{f: system[f] for f in ("total", "used", "available", "swap_used")},
                           "swap_used_10m_ago": swap_ago, "pressure": system["pressure"]},
                "processes": rows}


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
