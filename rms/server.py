import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from .store import pick_step


_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}


class _Bad(Exception):
    pass


def _int(q, key, required=False):
    v = q.get(key, [None])[-1]
    if v is None:
        if required:
            raise _Bad(f"missing {key}")
        return None
    if len(v) > 20 or not re.fullmatch(r"-?[0-9]+", v) or abs(int(v)) >= 2 ** 62:
        raise _Bad(f"{key} must be an integer")
    return int(v)


def _id(s):
    if len(s) > 20 or not re.fullmatch(r"-?[0-9]+", s) or not 0 < int(s) < 2 ** 62:
        raise _Bad("id must be a positive integer")
    return int(s)


def make_server(store, port, meta, web_dir, recent=None, sampler=None, proc_ids=None):
    web = Path(web_dir).resolve()
    if recent is not None:
        meta = {**meta, "features": ["processes", "live"]}
    interval = meta["interval"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype, api=False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            if api:
                self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code, obj):
            self._send(code, json.dumps(obj).encode(), "application/json", api=True)

        def _range(self, q):
            frm, to = _int(q, "from", True), _int(q, "to", True)
            if frm >= to:
                raise _Bad("from must be < to")
            step = _int(q, "step")
            if step is None:
                return frm, to, pick_step(frm, to, interval)
            if step <= 0:
                raise _Bad("step must be > 0")
            step = max(step, interval, -(-(to - frm) // 5000))
            return frm, to, -(-step // interval) * interval

        def _top(self, q, default):
            if q.get("top", [None])[-1] == "all":
                return None
            top = _int(q, "top")
            if top is None:
                return default
            if top <= 0:
                raise _Bad("top must be > 0")
            return top

        def do_GET(self):
            port = self.server.server_address[1]
            if self.headers.get("Host") not in (f"127.0.0.1:{port}", f"localhost:{port}"):
                return self._json(403, {"error": "forbidden host"})
            if self.headers.get("Sec-Fetch-Site", "same-origin") not in ("same-origin", "none"):
                return self._json(403, {"error": "cross-site request"})
            u = urlparse(self.path)
            try:
                if u.path.startswith("/api/"):
                    self._api(u.path, parse_qs(u.query))
                else:
                    self._static(u.path)
            except _Bad as e:
                self._json(400, {"error": str(e)})
            except Exception:  # never leak a traceback / drop the connection
                logging.getLogger("rms.server").exception("request failed: %s", self.path)
                self._json(500, {"error": "internal error"})

        def _api(self, path, q):
            if path == "/api/meta":
                st = store.stats()
                return self._json(200, {**meta, "mode": "live",
                                        "first_ts": st["first_ts"], "last_ts": st["last_ts"]})
            if path == "/api/system":
                # `to` is exclusive and passed through unchanged; live clients send now+1.
                frm, to, step = self._range(q)
                return self._json(200, {"step": step, "points": store.query_system(frm, to, step)})
            if path == "/api/apps":
                frm, to, step = self._range(q)
                top = self._top(q, 15)
                return self._json(200, {"step": step, "apps": store.query_apps(frm, to, step, top)})
            if path == "/api/processes":
                frm, to, step = self._range(q)
                app = q.get("app", [None])[-1]
                if app is not None and len(app) > 200:
                    raise _Bad("app too long")
                top = self._top(q, 25)
                return self._json(200, {"step": step, "processes":
                                        store.query_processes(frm, to, step, app, top)})
            m = re.fullmatch(r"/api/process/([^/]+)", path)
            if m:
                pid = _id(m.group(1))
                frm, to, step = self._range(q)
                d = store.get_process(pid, frm, to, step)
                if d is None:
                    return self._json(404, {"error": "not found"})
                par = d["parent"]
                if par and par["id"] is None and recent is not None:
                    par["name"] = recent.name_of(par["pid"], d["start_ts"])
                return self._json(200, d)
            if path == "/api/live" and recent is not None:
                if q.get("fast", [None])[-1] == "1" and sampler is not None:
                    sampler.poke()
                d = recent.snapshot(dict(proc_ids()) if proc_ids else {})
                if d["ts"] is None:
                    d = {**d, "resolution": None}
                return self._json(200, d)
            self._json(404, {"error": "not found"})

        def _static(self, path):
            rel = unquote(path).lstrip("/") or "index.html"
            try:
                f = (web / rel).resolve()
                ok = f.is_relative_to(web) and f.is_file()
            except ValueError:  # embedded NUL etc.
                ok = False
            if not ok:
                return self._send(404, b"not found", "text/plain")
            ctype = _TYPES.get(f.suffix.lower(), "application/octet-stream")
            self._send(200, f.read_bytes(), ctype)

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
