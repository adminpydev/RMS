import logging
import ntpath
import re
import subprocess
import sys
import time

import psutil

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
    def __init__(self, store, interval=10, retention_days=14):
        self.store, self.interval, self.retention = store, interval, retention_days
        self.tracker = Tracker()
        self.extras = (None, None)
        self.last_extras = -10**9
        self.last_prune = 0

    def tick(self, now):
        if sys.platform == "darwin" and now - self.last_extras >= 30:
            self.extras = mac_extras()
            self.last_extras = now
        pressure, compressed = self.extras if sys.platform == "darwin" else (None, None)
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        self.store.write_system(now, vm.total, vm.used, vm.available,
                                getattr(vm, "cached", None), compressed, sw.used, pressure)
        totals = sample_apps(psutil.process_iter(["pid", "name", "exe", "memory_info"]), sys.platform)
        self.store.write_apps(now, self.tracker.select(now, totals))
        if now - self.last_prune >= 3600:
            self.store.prune(now - self.retention * 86400)
            self.last_prune = now

    def run(self, stop):
        while not stop.is_set():
            try:
                self.tick(int(time.time()))
            except Exception:
                log.exception("collector tick failed")
            stop.wait(self.interval - time.time() % self.interval)
