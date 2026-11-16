import http.client
import json
import threading

import pytest

from rms.live import Recent
from rms.server import make_server

META = {"host": "h", "os": "darwin", "total_ram": 1000, "interval": 10, "version": "0.3.0"}
SYS = {"total": 100, "used": 50, "available": 50, "swap_used": 0, "pressure": 1}


class FakeSampler:
    pokes = 0

    def poke(self):
        self.pokes += 1


class P:
    def cmdline(self):
        return ["prog", "--x"]

    def username(self):
        return "me"


def row(pid, start, rss=1, ppid=1, name="n"):
    return {"pid": pid, "ppid": ppid, "start_ts": start, "name": name, "app": "A",
            "rss": rss, "cpu": 5, "threads": 1, "proc": P()}


@pytest.fixture
def env(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>hi</html>")
    (web / "app.js").write_text("1")
    recent, sampler = Recent(), FakeSampler()
    srv = make_server(0, META, web, recent, sampler)
    srv.recent, srv.sampler = recent, sampler
    t = threading.Thread(target=srv.serve_forever)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    t.join()


def req(srv, path, method="GET", host=None, headers=None):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request(method, path, headers={"Host": host or f"127.0.0.1:{port}", **(headers or {})})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


def test_binds_localhost_only(env):
    assert env.server_address[0] == "127.0.0.1"


def test_host_check(env):
    port = env.server_address[1]
    for p in ("/api/meta", "/api/live", "/api/process?pid=1&start_ts=1", "/"):
        assert req(env, p, host=f"evil.example:{port}")[0].status == 403
    assert req(env, "/", host="evil.example")[0].status == 403
    assert req(env, "/api/meta", host=f"localhost:{port}")[0].status == 200
    assert req(env, "/api/meta", host=f"127.0.0.1:{port}")[0].status == 200


def test_meta(env):
    r, body = req(env, "/api/meta")
    assert json.loads(body) == {**META, "mode": "live", "features": ["live"]}
    assert r.getheader("Cache-Control") == "no-store"


def test_no_cors_header(env):
    assert req(env, "/api/meta")[0].getheader("Access-Control-Allow-Origin") is None


def test_live(env):
    d = json.loads(req(env, "/api/live")[1])
    assert d == {"ts": None, "resolution": None, "simulated": False, "system": None,
                 "processes": []}
    env.recent.push(1000, SYS, {(5, 1): row(5, 1, rss=9)})
    r, body = req(env, "/api/live")
    d = json.loads(body)
    assert r.status == 200 and d["ts"] == 1000 and d["processes"][0]["rss"] == 9
    assert r.getheader("Cache-Control") == "no-store"
    assert env.sampler.pokes == 0
    req(env, "/api/live?fast=1")
    assert env.sampler.pokes == 1


def test_api_process(env):
    for ts in (1000, 1010):
        env.recent.push(ts, SYS, {(7, 50): row(7, 50, name="daddy"),
                                  (9, 60): row(9, 60, rss=ts, ppid=7, name="kid")})
    r, body = req(env, "/api/process?pid=9&start_ts=60")
    d = json.loads(body)
    assert r.status == 200 and r.getheader("Cache-Control") == "no-store"
    assert d == {"pid": 9, "ppid": 7, "start_ts": 60, "name": "kid", "app": "A", "user": "me",
                 "cmdline": "prog --x", "alive": True, "parent": {"pid": 7, "name": "daddy"},
                 "children": [], "series": [{"ts": 1000, "rss": 1000, "cpu": 5},
                                            {"ts": 1010, "rss": 1010, "cpu": 5}]}


def test_api_process_404_after_age_out(env):
    env.recent.push(1000, SYS, {(9, 60): row(9, 60)})
    env.recent.push(1010, SYS, {(7, 50): row(7, 50)})
    assert json.loads(req(env, "/api/process?pid=9&start_ts=60")[1])["alive"] is False
    env.recent.push(1010 + 1801, SYS, {(7, 50): row(7, 50)})
    r, body = req(env, "/api/process?pid=9&start_ts=60")
    assert r.status == 404 and json.loads(body) == {"error": "not found"}


@pytest.mark.parametrize("q", ["pid=1&start_ts=2", "pid=0&start_ts=0"])
def test_api_process_unknown_404(env, q):
    r, body = req(env, "/api/process?" + q)
    assert r.status == 404 and json.loads(body) == {"error": "not found"}


@pytest.mark.parametrize("q", [
    "", "pid=1", "start_ts=1", "pid=abc&start_ts=1", "pid=1&start_ts=x", "pid=-1&start_ts=1",
    "pid=1&start_ts=-5", "pid=1.5&start_ts=1", "pid=%2B5&start_ts=1", "pid=%205&start_ts=1",
    "pid=1_000&start_ts=1", "pid=1&start_ts=" + "9" * 21, "pid=4611686018427387904&start_ts=1",
    "pid=" + "9" * 5000 + "&start_ts=1",
])
def test_api_process_bad_params_400(env, q):
    r, body = req(env, "/api/process?" + q)
    assert r.status == 400 and "error" in json.loads(body)


@pytest.mark.parametrize("p", ["/api/system?from=0&to=10", "/api/apps?from=0&to=10",
                               "/api/processes?from=0&to=10", "/api/process/1",
                               "/api/process/1?from=0&to=10", "/api/nope"])
def test_removed_endpoints_404(env, p):
    r, body = req(env, p)
    assert r.status == 404 and json.loads(body) == {"error": "not found"}


def test_no_mutating_methods(env):
    for m in ("POST", "PUT", "DELETE"):
        for p in ("/api/live", "/api/process?pid=1&start_ts=1"):
            assert not 200 <= req(env, p, method=m)[0].status < 300


@pytest.mark.parametrize("site,code", [("cross-site", 403), ("same-site", 403),
                                       ("same-origin", 200), ("none", 200), (None, 200)])
def test_sec_fetch_site(env, site, code):
    for path in ("/api/live", "/"):
        r, _ = req(env, path, headers={"Sec-Fetch-Site": site} if site else None)
        if code == 403:
            assert r.status == 403
        else:
            assert r.status == 200


def test_500_no_leak(env, monkeypatch):
    monkeypatch.setattr(env.recent, "snapshot", lambda: 1 / 0)
    r, body = req(env, "/api/live")
    assert r.status == 500 and json.loads(body) == {"error": "internal error"}


def test_static_and_traversal(env):
    r, body = req(env, "/")
    assert r.status == 200 and b"hi" in body and "html" in r.getheader("Content-Type")
    assert req(env, "/../pyproject.toml")[0].status == 404
    assert req(env, "/%2e%2e/x")[0].status == 404
    assert req(env, "/%2e%2e/%2e%2e/etc/passwd")[0].status == 404
    assert req(env, "/nope")[0].status == 404


def test_static_nul_404(env):
    assert req(env, "/a%00b")[0].status == 404


def test_static_js_content_type(env):
    r, _ = req(env, "/app.js")
    assert r.getheader("Content-Type").startswith("text/javascript")
