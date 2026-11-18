import json
import logging
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


_TYPES = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
          ".css": "text/css; charset=utf-8", ".json": "application/json", ".svg": "image/svg+xml"}


class _Bad(Exception):
    pass


def _int(q, key):
    v = q.get(key, [None])[-1]
    if v is None:
        raise _Bad(f"missing {key}")
    if len(v) > 20 or not re.fullmatch(r"-?[0-9]+", v) or abs(int(v)) >= 2 ** 62:
        raise _Bad(f"{key} must be an integer")
    return int(v)


def make_server(port, meta, web_dir, snap):
    web = Path(web_dir).resolve()
    meta = {**meta, "mode": "live", "features": ["now"]}

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
                return self._json(200, meta)
            if path == "/api/live":
                return self._json(200, snap.take())
            if path == "/api/process":
                pid, start_ts = _int(q, "pid"), _int(q, "start_ts")
                if pid < 0 or start_ts < 0:
                    raise _Bad("pid and start_ts must be >= 0")
                d = snap.process_detail(pid, start_ts)
                if d is not None:
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
