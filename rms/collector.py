import logging
import ntpath
import re
import subprocess
import sys
import threading
import time

import psutil

from rms.live import Recent
from rms.store import OTHER_SMALL

log = logging.getLogger("rms.collector")
_SKIP = (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess)


def app_name(name, exe, platform):
    if exe:
        if platform == "darwin":
            for seg in exe.split("/"):
                if seg.endswith(".app"):
                    return seg[:-4]
        elif platform == "win32":
            base = ntpath.basename(exe)
            return base[:-4] if base.lower().endswith(".exe") else base
    return name


def sample_apps(procs, platform):
    out = {}
    for p in procs:
        try:
            info = p.info
            mem = info["memory_info"]
            if mem is None:
                continue
            app = app_name(info["name"], info["exe"], platform)
            if not app:
                continue
        except _SKIP:
            continue
        rss, n = out.get(app, (0, 0))
        out[app] = (rss + mem.rss, n + 1)
    return out


def sample_processes(procs, platform):
    out = {}
    for p in procs:
        try:
            info = p.info
            mem, ct = info["memory_info"], info["create_time"]
            if mem is None or ct is None:
                continue
            app = app_name(info["name"], info["exe"], platform)
            if not app:
                continue
        except _SKIP:
            continue
        start = int(ct)
        out[(info["pid"], start)] = {"pid": info["pid"], "ppid": info["ppid"], "start_ts": start,
                                     "name": info["name"] or app, "app": app, "rss": mem.rss,
                                     "threads": info["num_threads"], "proc": p}
    return out


class ProcessTracker:
    def __init__(self, top=50, threshold=50 * 2**20, hold=3600):
        self.top, self.threshold, self.hold = top, threshold, hold
        self.last_pass = {}

    def select(self, ts, procs):
        for k in sorted(procs, key=procs.get, reverse=True)[:self.top]:
            self.last_pass[k] = ts
        for k, rss in procs.items():
            if rss > self.threshold:
                self.last_pass[k] = ts
        self.last_pass = {k: t for k, t in self.last_pass.items() if ts - t < self.hold}
        return {k for k in procs if k in self.last_pass}


def _meta_read(fn):
    try:
        return fn()
    except _SKIP:
        return None


class Tracker:
    def __init__(self, top=25, threshold=100 * 2**20, hold=3600):
        self.top, self.threshold, self.hold = top, threshold, hold
        self.last_pass = {}

    def select(self, ts, totals):
        ranked = sorted(totals, key=lambda a: totals[a][0], reverse=True)[:self.top]
        for a in ranked:
            self.last_pass[a] = ts
        for a, (rss, _) in totals.items():
            if rss > self.threshold:
                self.last_pass[a] = ts
        self.last_pass = {a: t for a, t in self.last_pass.items() if ts - t < self.hold}
        rows, o_rss, o_n = [], 0, 0
        for a, (rss, n) in totals.items():
            if a in self.last_pass:
                rows.append((a, rss, n))
            else:
                o_rss, o_n = o_rss + rss, o_n + n
        if o_n:
            rows.append((OTHER_SMALL, o_rss, o_n))
        return rows


def _run(*cmd):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=2, check=True).stdout


def mac_extras():
    try:
        pressure = int(_run("sysctl", "-n", "kern.memorystatus_vm_pressure_level").strip())
        out = _run("vm_stat")
        page = int(re.search(r"page size of (\d+) bytes", out).group(1))
        pages = int(re.search(r"Pages occupied by compressor:\s+(\d+)", out).group(1))
        return pressure, pages * page
    except Exception:
        return None, None


class Collector:
    _ATTRS = ["pid", "ppid", "name", "exe", "memory_info", "create_time", "num_threads"]

    def __init__(self, store, interval=10, retention_days=14, recent=None):
        self.store, self.interval, self.retention = store, interval, retention_days
        self.tracker = Tracker()
        self.ptracker = ProcessTracker()
        self.recent = recent if recent is not None else Recent()
        self.proc_ids = {}
        self._ps = {}  # key -> psutil.Process for cpu_percent; shared with the live sampler
        self._ps_lock = threading.Lock()
        self.extras = (None, None)
        self.last_extras = -10**9
        self.last_prune = 0

    def _system(self, vm, sw):
        pressure = self.extras[0] if sys.platform == "darwin" else None
        return {"total": vm.total, "used": vm.used, "available": vm.available,
                "swap_used": sw.used, "pressure": pressure}

    def _scan(self, now, system):
        """One process_iter pass + cpu + recent.push; no DB writes. Returns (list, procs)."""
        with self._ps_lock:
            plist = list(psutil.process_iter(self._ATTRS))
            procs = sample_processes(plist, sys.platform)
            for k, v in procs.items():
                cached = self._ps.get(k)
                if cached is None:
                    self._ps[k] = v["proc"]
                    _meta_read(lambda: v["proc"].cpu_percent(None))  # prime; first reading discarded
                    v["cpu"] = None
                else:
                    # ponytail: one shared Process per key, so while a Live tab is open the 10 s
                    # tick's cpu_percent window shrinks to the time since the last 2 s sampler
                    # call (stored cpu noisier); upgrade: separate Process objects per caller.
                    pct = _meta_read(lambda: cached.cpu_percent(None))
                    v["cpu"] = None if pct is None else int(pct * 10)
            self._ps = {k: p for k, p in self._ps.items() if k in procs}
        self.recent.push(now, system, procs)
        return plist, procs

    def scan_live(self, now):
        self._scan(now, self._system(psutil.virtual_memory(), psutil.swap_memory()))

    def tick(self, now):
        if sys.platform == "darwin" and now - self.last_extras >= 30:
            self.extras = mac_extras()
            self.last_extras = now
        pressure, compressed = self.extras if sys.platform == "darwin" else (None, None)
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        self.store.write_system(now, vm.total, vm.used, vm.available,
                                getattr(vm, "cached", None), compressed, sw.used, pressure)
        plist, procs = self._scan(now, self._system(vm, sw))
        self.store.write_apps(now, self.tracker.select(now, sample_apps(plist, sys.platform)))
        tracked = self.ptracker.select(now, {k: v["rss"] for k, v in procs.items()})
        new = [k for k in tracked if k not in self.proc_ids]
        if new:
            rows = []
            for k in new:
                v = procs[k]
                cmd = _meta_read(v["proc"].cmdline)
                rows.append((v["pid"], v["ppid"], v["start_ts"], v["name"], v["app"],
                             _meta_read(v["proc"].username),
                             " ".join(cmd) if cmd is not None else None))
            # replace, don't mutate: the live API copies proc_ids from another thread
            self.proc_ids = {**self.proc_ids, **dict(zip(new, self.store.add_processes(rows, now)))}
        self.store.write_process_samples(now, [
            (self.proc_ids[k], procs[k]["rss"], procs[k]["cpu"], procs[k]["threads"]) for k in tracked])
        self.store.touch_processes(now, [self.proc_ids[k] for k in procs if k in self.proc_ids])
        if now - self.last_prune >= 3600:
            self.store.prune(now - self.retention * 86400)
            gone = set(self.store.last_pruned_ids)
            self.proc_ids = {k: i for k, i in self.proc_ids.items() if i not in gone}
            self.last_prune = now

    def run(self, stop):
        while not stop.is_set():
            try:
                self.tick(int(time.time()))
            except Exception:
                log.exception("collector tick failed")
            stop.wait(self.interval - time.time() % self.interval)
