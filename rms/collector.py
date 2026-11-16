import logging
import ntpath
import re
import subprocess
import sys
import threading
import time

import psutil

from rms.live import Recent

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


def _meta_read(fn):
    try:
        return fn()
    except _SKIP:
        return None


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

    def __init__(self, recent, interval=10):
        self.recent, self.interval = recent, interval
        self._ps = {}  # key -> psutil.Process for cpu_percent; shared with the live sampler
        self._ps_lock = threading.Lock()
        self.extras = (None, None)
        self.last_extras = -10**9

    def _system(self):
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        pressure = self.extras[0] if sys.platform == "darwin" else None
        return {"total": vm.total, "used": vm.used, "available": vm.available,
                "swap_used": sw.used, "pressure": pressure}

    def scan_live(self, now):
        """One process_iter pass + cpu + recent.push."""
        system = self._system()
        with self._ps_lock:
            procs = sample_processes(psutil.process_iter(self._ATTRS), sys.platform)
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

    def tick(self, now):
        if sys.platform == "darwin" and now - self.last_extras >= 30:
            self.extras = mac_extras()
            self.last_extras = now
        self.scan_live(now)

    def run(self, stop):
        while not stop.is_set():
            try:
                self.tick(int(time.time()))
            except Exception:
                log.exception("collector tick failed")
            stop.wait(self.interval - time.time() % self.interval)
