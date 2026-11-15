import http.client
import json
import threading

import pytest

from rms.server import make_server
from rms.store import Store

META = {"host": "h", "os": "darwin", "total_ram": 1000, "interval": 10, "version": "0.1.0"}


@pytest.fixture
def env(tmp_path):
    web = tmp_path / "web"
    web.mkdir()
    (web / "index.html").write_text("<html>hi</html>")
    store = Store(str(tmp_path / "rms.db"))
    srv = make_server(store, 0, META, web)
    t = threading.Thread(target=srv.serve_forever)
    t.start()
    yield srv, store
    srv.shutdown()
    srv.server_close()
    t.join()


def get(srv, path, host=None):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request("GET", path, headers={"Host": host or f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


def test_binds_localhost_only(env):
    assert env[0].server_address[0] == "127.0.0.1"


def test_host_check(env):
    srv = env[0]
    port = srv.server_address[1]
    assert get(srv, "/api/meta", f"evil.example:{port}")[0].status == 403
    assert get(srv, "/", "evil.example")[0].status == 403
    assert get(srv, "/api/meta", f"localhost:{port}")[0].status == 200
    assert get(srv, "/api/meta", f"127.0.0.1:{port}")[0].status == 200


def test_meta_empty_db(env):
    r, body = get(env[0], "/api/meta")
    d = json.loads(body)
    assert d["mode"] == "live" and d["first_ts"] is None and d["host"] == "h"
    assert r.getheader("Cache-Control") == "no-store"


def test_system_and_apps_shapes(env):
    srv, store = env
    store.write_system(10, 1000, 500, 500, None, None, 0, None)
    store.write_apps(10, [("Chrome", 100, 3)])
    d = json.loads(get(srv, "/api/system?from=0&to=100")[1])
    assert "step" in d
    assert set(d["points"][0]) == {"ts", "used", "available", "cached", "compressed",
                                   "swap_used", "pressure", "used_max"}
    d = json.loads(get(srv, "/api/apps?from=0&to=100&top=all")[1])
    assert set(d["apps"]["Chrome"][0]) == {"ts", "rss", "rss_max", "procs"}
    assert json.loads(get(srv, "/api/apps?from=0&to=100&top=1")[1])["apps"]


def test_step_clamped_to_interval_multiple(env):
    d = json.loads(get(env[0], "/api/system?from=0&to=100&step=3")[1])
    assert d["step"] == 10


@pytest.mark.parametrize("q", [
    "/api/system?from=abc&to=10", "/api/system?from=100&to=0", "/api/system?from=5",
    "/api/system?from=0&to=10&step=0", "/api/apps?from=0&to=10&top=0",
    "/api/apps?from=0&to=10&top=x", "/api/system?from=0&to=1_000", "/api/system?from=0&to=%2B5",
    "/api/system?from=0&to=%205", "/api/system?from=0&to=10&step=99999999999999999999",
    "/api/system?from=-99999999999999999999&to=10", "/api/system?from=0&to=10&step=1.5",
])
def test_bad_params_400(env, q):
    r, body = get(env[0], q)
    assert r.status == 400 and "error" in json.loads(body)


def test_static_and_traversal(env):
    srv = env[0]
    r, body = get(srv, "/")
    assert r.status == 200 and b"hi" in body and "html" in r.getheader("Content-Type")
    assert get(srv, "/../pyproject.toml")[0].status == 404
    assert get(srv, "/%2e%2e/x")[0].status == 404
    assert get(srv, "/%2e%2e/%2e%2e/etc/passwd")[0].status == 404
    assert get(srv, "/nope")[0].status == 404


def test_no_cors_header(env):
    assert get(env[0], "/api/meta")[0].getheader("Access-Control-Allow-Origin") is None


def test_apps_default_top(env):
    srv, store = env
    store.write_system(10, 1000, 500, 500, None, None, 0, None)
    store.write_apps(10, [(f"a{i}", 100 + i, 1) for i in range(20)] + [("Other (small apps)", 5, 1)])
    r, body = get(srv, "/api/apps?from=0&to=100")
    assert r.status == 200
    assert len(json.loads(body)["apps"]) == 16


def test_static_nul_404(env):
    assert get(env[0], "/a%00b")[0].status == 404
