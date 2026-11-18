"""What is true right now: one psutil pass per request, no history, no background thread."""
import ntpath
import re
import socket
import subprocess
import sys
import threading
import time

import psutil

from rms.redact import redact

_SKIP = (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess)
_ATTRS = ["pid", "ppid", "name", "exe", "username", "memory_info", "num_threads", "status",
          "create_time"]
_EXTRAS_TTL = 5
_TAKE_TTL = 1  # take() returns the previous result if younger than this
_TABLE_TTL = 5  # process_detail reuses the last take()'s table if younger than this
_MAX_PORTS = 50


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


def _read(fn):
    try:
        return fn()
    except _SKIP:
        return None


def _cmdline(proc):
    cmd = _read(proc.cmdline)
    return redact(" ".join(cmd))[:2000] if cmd is not None else None  # cap 2000


def _ports(conns):
    seen = set()
    for c in conns:
        if not c.laddr or not c.laddr.port:  # macOS unbound udp: ('0.0.0.0', 0)
            continue
        if c.type == socket.SOCK_STREAM and c.status == psutil.CONN_LISTEN:
            seen.add((c.laddr.port, "tcp", c.laddr.ip))
        elif c.type == socket.SOCK_DGRAM:
            seen.add((c.laddr.port, "udp", c.laddr.ip))
    return [{"port": p, "proto": pr, "addr": a} for p, pr, a in sorted(seen)[:_MAX_PORTS]]


class Snapshotter:
    def __init__(self, platform=sys.platform, clock=time.monotonic):
        self.platform, self.clock = platform, clock
        # ponytail: one lock around everything, incl. first-sight cmdline reads (~25 ms scans);
        # move cmdline reads outside the lock if concurrent polls ever queue noticeably.
        self._lock = threading.Lock()
        self._cache = {}  # (pid, start_ts) -> {"proc": psutil.Process, "cmdline": str|None}
        self._extras, self._extras_at = (None, None), None
        self._last, self._last_at = None, None
        self._table, self._table_at = {}, None  # last take(): pid -> (ppid, start_ts, name)

    # ponytail: runs sysctl + vm_stat under the take lock; if they hang, polls stall up to
    # 2x2 s every 5 s. Upgrade: refresh outside the lock and swap the result in.
    def _mac_extras(self):
        now = self.clock()
        if self._extras_at is None or now - self._extras_at >= _EXTRAS_TTL:
            self._extras, self._extras_at = mac_extras(), now
        return self._extras

    def _system(self):
        vm, sw = psutil.virtual_memory(), psutil.swap_memory()
        pressure = None
        if self.platform == "darwin":
            pressure, compressed = self._mac_extras()
            b = {"wired": vm.wired, "active": vm.active, "inactive": vm.inactive,
                 "compressed": compressed or 0}
            b["free"] = max(0, vm.total - sum(b.values()))
        else:
            b = {"used": vm.used}
            if hasattr(vm, "cached"):
                b["cached"] = vm.cached
            b["free"] = vm.free
        used = vm.used
        if self.platform == "darwin":  # psutil omits compressed; match Activity Monitor
            used = b["wired"] + b["active"] + b["compressed"]
        return {"total": vm.total, "used": used, "available": vm.available,
                "swap_total": sw.total, "swap_used": sw.used, "pressure": pressure, "breakdown": b}

    def take(self):
        with self._lock:
            now = self.clock()
            if self._last is not None and now - self._last_at < _TAKE_TTL:
                return self._last  # ponytail: two polling tabs cost ~one scan
            self._last = self._take()
            self._last_at = now
            return self._last

    def _take(self):
        system = self._system()
        total = system["total"]
        rows, live, apps = [], {}, {}
        for p in psutil.process_iter(_ATTRS):
            try:
                info = p.info
                mem, ct = info["memory_info"], info["create_time"]
                if ct is None:  # can't key it
                    continue
                app = app_name(info["name"], info["exe"], self.platform)
                if not app:
                    continue
            except _SKIP:
                continue
            key = (info["pid"], int(ct))
            entry = self._cache.get(key)
            if entry is None:
                entry = {"proc": p, "cmdline": _cmdline(p)}
                _read(lambda: p.cpu_percent(None))  # prime; first reading discarded
                cpu = None
            else:
                pct = _read(lambda: entry["proc"].cpu_percent(None))
                cpu = None if pct is None else round(pct * 10)
            live[key] = entry
            rss = None if mem is None else mem.rss  # denied (other users): row with nulls
            rows.append({"pid": key[0], "ppid": info["ppid"], "start_ts": key[1],
                         "name": info["name"] or app, "app": app, "user": info["username"],
                         "cmdline": entry["cmdline"], "rss": rss,
                         "share": None if rss is None else round(rss / total, 4), "cpu": cpu,
                         "threads": info["num_threads"], "status": info["status"]})
            a = apps.setdefault(app, [0, 0])
            a[0] += rss or 0
            a[1] += 1
        self._cache = live
        self._table = {r["pid"]: (r["ppid"], r["start_ts"], r["name"]) for r in rows}
        self._table_at = self.clock()
        rows.sort(key=lambda r: (r["rss"] is None, -(r["rss"] or 0)))
        app_rows = [{"app": k, "rss": r, "share": round(r / total, 4), "procs": n}
                for k, (r, n) in apps.items()]
        app_rows.sort(key=lambda a: a["rss"], reverse=True)
        return {"ts": int(time.time()), "simulated": False, "system": system,
            "apps": app_rows, "processes": rows}

    def process_detail(self, pid, start_ts):
        if pid < 0:
            return None  # psutil.Process(-1) raises ValueError
        with self._lock:
            try:
                return self._detail(pid, start_ts)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                return None  # gone (ZombieProcess fields are handled per read) or unverifiable

    def _detail(self, pid, start_ts):
        p = psutil.Process(pid)
        if int(p.create_time()) != start_ts:
            return None
        restricted = []

        def r(fn):  # NoSuchProcess propagates -> None
            try:
                return fn()
            except (psutil.AccessDenied, psutil.ZombieProcess):
                restricted.append(fn)
                return None

        name, exe = r(p.name), r(p.exe)
        cmd = r(p.cmdline)
        mem, cpu = r(p.memory_info), r(p.cpu_times)
        out = {"pid": pid, "ppid": r(p.ppid), "start_ts": start_ts, "name": name,
               "app": app_name(name, exe, self.platform), "user": r(p.username),
               "cmdline": redact(" ".join(cmd))[:2000] if cmd is not None else None,
               "exe": exe, "cwd": r(p.cwd), "status": r(p.status),
               "rss": getattr(mem, "rss", None), "uss": r(lambda: p.memory_full_info().uss),
               "vms": getattr(mem, "vms", None), "pfaults": getattr(mem, "pfaults", None),
               "pageins": getattr(mem, "pageins", None),
               "cpu_user": getattr(cpu, "user", None), "cpu_system": getattr(cpu, "system", None),
               "threads": r(p.num_threads), "open_files": r(lambda: len(p.open_files())),
               "ports": r(lambda: _ports(p.net_connections(kind="inet")))}
        table = self._table
        if self._table_at is None or self.clock() - self._table_at >= _TABLE_TTL:
            table = {}
            for q in psutil.process_iter(["pid", "ppid", "name", "create_time"]):
                i = _read(lambda: q.info)
                if i and i["create_time"] is not None:
                    table[i["pid"]] = (i["ppid"], int(i["create_time"]), i["name"])
        par = table.get(out["ppid"])
        out["parent"] = ({"pid": out["ppid"], "start_ts": par[1], "name": par[2]}
                         if par and out["ppid"] != pid and par[1] <= start_ts else None)
        out["children"] = [{"pid": k, "start_ts": st, "name": n}
                           for k, (pp, st, n) in sorted(table.items())
                           if pp == pid and k != pid and st >= start_ts]
        out["restricted"] = bool(restricted)
        return out
