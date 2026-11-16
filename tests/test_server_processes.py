import http.client
import json
import threading

import psutil
import pytest

from rms.live import Recent
from rms.server import make_server
from rms.store import Store

META = {"host": "h", "os": "darwin", "total_ram": 1000, "interval": 10, "version": "0.2.0"}
SYS = {"total": 100, "used": 50, "available": 50, "swap_used": 0, "pressure": 1}


class FakeSampler:
    pokes = 0

    def poke(self):
        self.pokes += 1


def start(tmp_path, **kw):
    web = tmp_path / "web"
    web.mkdir(exist_ok=True)
    store = Store(str(tmp_path / "rms.db"))
    srv = make_server(store, 0, META, web, **kw)
    t = threading.Thread(target=srv.serve_forever)
    t.start()
    return srv, store, t


@pytest.fixture
def env(tmp_path):
    recent, sampler, ids = Recent(), FakeSampler(), {}
    srv, store, t = start(tmp_path, recent=recent, sampler=sampler, proc_ids=lambda: ids)
    srv.recent, srv.sampler, srv.ids = recent, sampler, ids
    yield srv, store
    srv.shutdown()
    srv.server_close()
    t.join()


def req(srv, path, method="GET", host=None):
    port = srv.server_address[1]
    c = http.client.HTTPConnection("127.0.0.1", port)
    c.request(method, path, headers={"Host": host or f"127.0.0.1:{port}"})
    r = c.getresponse()
    body = r.read()
    c.close()
    return r, body


def seed(store):
    a = store.add_process(10, 1, 100, "a", "A", "u", "cmd", 1000)
    b = store.add_process(11, 10, 100, "b", "A", "u", "cmd", 1000)
    for ts in (1000, 1010):
        store.write_system(ts, 1, 1, 1, 0, 0, 0, 1)
        store.write_process_samples(ts, [(a, 500, 1, 2), (b, 100, 1, 1)])
    return a, b


def test_processes(env):
    srv, store = env
    a, b = seed(store)
    r, body = req(srv, "/api/processes?from=1000&to=1020&step=10")
    d = json.loads(body)
    assert r.status == 200 and set(d["processes"]) == {str(a), str(b)}
    assert d["processes"][str(a)]["series"][0]["rss"] == 500
    d = json.loads(req(srv, "/api/processes?from=1000&to=1020&step=10&top=1")[1])
    assert list(d["processes"]) == [str(a)]
    d = json.loads(req(srv, "/api/processes?from=1000&to=1020&step=10&top=all&app=A")[1])
    assert len(d["processes"]) == 2
    d = json.loads(req(srv, "/api/processes?from=1000&to=1020&step=10&app=zzz")[1])
    assert d["processes"] == {}


@pytest.mark.parametrize("q", ["app=" + "x" * 201, "top=0", "top=abc", "app=x&top=-1"])
def test_processes_bad(env, q):
    assert req(env[0], f"/api/processes?from=1000&to=1020&{q}")[0].status == 400
    assert req(env[0], "/api/processes?from=1")[0].status == 400


def test_process_detail(env):
    srv, store = env
    a, b = seed(store)
    r, body = req(srv, f"/api/process/{b}?from=1000&to=1020&step=10")
    d = json.loads(body)
    assert r.status == 200 and d["id"] == b and d["parent"]["id"] == a
    assert d["series"][0]["rss"] == 100


def test_process_detail_parent_from_recent(env):
    srv, store = env
    c = store.add_process(20, 777, 100, "c", "C", "u", "cmd", 1000)
    srv.recent.push(1000, SYS, {(777, 5): {"rss": 1, "cpu": 0, "threads": 1, "pid": 777, "ppid": 1,
                                           "start_ts": 5, "name": "daddy", "app": "D", "proc": None}})
    d = json.loads(req(srv, f"/api/process/{c}?from=1000&to=1020&step=10")[1])
    assert d["parent"] == {"id": None, "pid": 777, "name": "daddy"}


@pytest.mark.parametrize("p,code", [("abc", 400), ("0", 400), ("-3", 400), ("9" * 21, 400),
                                    ("999", 404), ("1/2", 404)])
def test_process_bad_id(env, p, code):
    r, body = req(env[0], f"/api/process/{p}?from=1000&to=1020")
    assert r.status == code
    if code == 404:
        assert json.loads(body) == {"error": "not found"}


def test_process_range_required(env):
    assert req(env[0], "/api/process/1")[0].status == 400


def test_live(env):
    srv, _ = env
    d = json.loads(req(srv, "/api/live")[1])
    assert d == {"ts": None, "resolution": None, "simulated": False, "system": None,
                 "processes": []}
    srv.recent.push(1000, SYS, {(5, 1): {"rss": 9, "cpu": 0, "threads": 1, "pid": 5, "ppid": 1,
                                         "start_ts": 1, "name": "n", "app": "N", "proc": psutil.Process()}})
    srv.ids[(5, 1)] = 42
    r, body = req(srv, "/api/live")
    d = json.loads(body)
    assert r.status == 200 and d["ts"] == 1000 and d["processes"][0]["id"] == 42
    assert srv.sampler.pokes == 0
    req(srv, "/api/live?fast=1")
    assert srv.sampler.pokes == 1


def test_live_without_recent(tmp_path):
    srv, store, t = start(tmp_path)
    try:
        assert req(srv, "/api/live")[0].status == 404
        assert "features" not in json.loads(req(srv, "/api/meta")[1])
    finally:
        srv.shutdown()
        srv.server_close()
        t.join()


def test_meta_features(env):
    assert json.loads(req(env[0], "/api/meta")[1])["features"] == ["processes", "live"]


def test_host_check(env):
    for p in ("/api/processes?from=1&to=2", "/api/process/1", "/api/live"):
        assert req(env[0], p, host="evil.com")[0].status == 403


def test_no_mutating_methods(env):
    for m in ("POST", "PUT", "DELETE"):
        for p in ("/api/live", "/api/process/1"):
            assert not 200 <= req(env[0], p, method=m)[0].status < 300
