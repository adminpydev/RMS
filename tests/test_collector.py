import psutil
import pytest

from rms import collector
from rms.collector import Collector, Tracker, app_name, sample_apps
from rms.store import OTHER_SMALL

CHROME_EXE = ("/Applications/Google Chrome.app/Contents/Frameworks/Google Chrome Framework.framework/"
              "Helpers/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)")


@pytest.mark.parametrize("name,exe,plat,want", [
    ("Google Chrome Helper (Renderer)", CHROME_EXE, "darwin", "Google Chrome"),
    ("Code Helper", "/Applications/Visual Studio Code.app/Contents/MacOS/Code Helper", "darwin", "Visual Studio Code"),
    ("python3", "/usr/bin/python3", "darwin", "python3"),
    ("chrome.exe", "C:\\Program Files\\Google\\Chrome\\chrome.exe", "win32", "chrome"),
    ("firefox", "/usr/lib/firefox/firefox", "linux", "firefox"),
    ("kernel_task", None, "darwin", "kernel_task"),
])
def test_app_name_rules(name, exe, plat, want):
    assert app_name(name, exe, plat) == want


class FakeProc:
    def __init__(self, info=None, exc=None):
        self._info, self._exc = info, exc

    @property
    def info(self):
        if self._exc:
            raise self._exc
        return self._info


def proc(rss, name="Google Chrome Helper (Renderer)", exe=CHROME_EXE):
    return FakeProc({"name": name, "exe": exe, "memory_info": type("M", (), {"rss": rss})()})


def test_sample_apps_groups_and_sums():
    assert sample_apps([proc(100), proc(50)], "darwin") == {"Google Chrome": (150, 2)}


def test_sample_apps_skips_errors():
    procs = [FakeProc(exc=psutil.AccessDenied()), FakeProc(exc=psutil.NoSuchProcess(1)),
             FakeProc({"name": "x", "exe": None, "memory_info": None}), proc(7)]
    assert sample_apps(procs, "darwin") == {"Google Chrome": (7, 1)}


def test_sample_apps_skips_nameless():
    assert sample_apps([proc(5, name=None, exe=None)], "darwin") == {}


def test_tracker_hysteresis():
    t = Tracker(top=1, threshold=1000, hold=3600)
    assert t.select(0, {"A": (2000, 1)}) == [("A", 2000, 1)]
    rows = t.select(10, {"A": (10, 1), "B": (50, 1)})
    assert {r[0] for r in rows} == {"A", "B"}
    rows = t.select(3610, {"A": (10, 1), "B": (50, 1), "C": (5, 2)})
    assert sorted(rows) == [("B", 50, 1), (OTHER_SMALL, 15, 3)]


class FakeStore:
    def __init__(self):
        self.calls = []

    def write_system(self, *a):
        self.calls.append(("system", a))

    def write_apps(self, *a):
        self.calls.append(("apps", a))

    def prune(self, before):
        self.calls.append(("prune", before))
        return 0


def test_collector_tick_writes(monkeypatch):
    vm = type("VM", (), {"total": 100, "used": 40, "available": 60})()
    monkeypatch.setattr(psutil, "virtual_memory", lambda: vm)
    monkeypatch.setattr(psutil, "swap_memory", lambda: type("S", (), {"used": 5})())
    monkeypatch.setattr(psutil, "process_iter", lambda attrs=None: [proc(100)])
    monkeypatch.setattr(collector, "mac_extras", lambda: (1, 9))
    monkeypatch.setattr(collector.sys, "platform", "darwin")
    st = FakeStore()
    c = Collector(st)
    c.tick(1000)
    c.tick(1010)
    kinds = [k for k, _ in st.calls]
    assert kinds.count("system") == 2 and kinds.count("apps") == 2
    sysargs = [a for k, a in st.calls if k == "system"]
    assert sysargs[0] == (1000, 100, 40, 60, None, 9, 5, 1)
    assert sysargs[1][5:8] == (9, 5, 1)  # extras reused
    assert [a for k, a in st.calls if k == "apps"][0][1] == [("Google Chrome", 100, 1)]
