"""Five deterministic example snapshots for the public demo (site/sample.json).

Run: python scripts/make_demo.py
Each snapshot is exactly an /api/live body plus details{"pid:start_ts": /api/process body}
for every process. Same machine, five moments (spec section 9):
S1 calm; S2 Docker VM > 20% of RAM; S3 Chrome > 30% with > 30 processes;
S4 memory pressure + swap > 1 GiB; S5 two zombies. Nothing else crosses an insight threshold.
All numbers are ints except share (4 decimals).
"""
import json
import random
from pathlib import Path

from rms import __version__
from rms.redact import redact
from rms.snapshot import app_name

MIB, GIB = 2**20, 2**30
TOTAL = 16 * GIB
T1 = 1794988800  # 2026-11-18 00:00 UTC, S1 ts; later moments follow at 1 h steps
BOOT = T1 - 3 * 86400

CH = "/Applications/Google Chrome.app/Contents"
CHH = CH + "/Frameworks/Google Chrome Framework.framework/Helpers/Google Chrome Helper"
RND = " --type=renderer --lang=en-US --renderer-client-id="


def _app(base, helper=None):  # path of an app bundle's main binary, or of one of its helpers
    if helper is None:
        return f"/Applications/{base}.app/Contents/MacOS/{base}"
    return (f"/Applications/{base}.app/Contents/Frameworks/{helper}.app/Contents/MacOS/{helper}")


def _chrome_renderer(pid, ppid, mib, cid):
    exe = CHH + " (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer)"
    return (pid, ppid, "Google Chrome Helper (Renderer)", "demo", exe, exe + RND + str(cid),
            mib, (20, 250), 17, "")


# pid, ppid, name, user, exe, cmdline, MiB, cpu range (percent x 10), threads, flags
# flags: "d" = memory denied (rss/cpu/threads null), "z" = zombie
PROCS = [
    (0, 0, "kernel_task", "root", None, None, 450, (20, 120), 240, ""),
    (1, 0, "launchd", "root", "/sbin/launchd", "/sbin/launchd", 20, (0, 10), 4, ""),
    (88, 1, "logd", "root", "/usr/libexec/logd", "/usr/libexec/logd", 30, (0, 20), 5, ""),
    (92, 1, "UserEventAgent", "root", "/usr/libexec/UserEventAgent",
     "/usr/libexec/UserEventAgent (System)", 15, (0, 5), 3, ""),
    (95, 1, "configd", "root", "/usr/libexec/configd", "/usr/libexec/configd", 12, (0, 5), 9, ""),
    (101, 1, "powerd", "root", "/System/Library/CoreServices/powerd.bundle/powerd", None, 8,
     (0, 0), 3, "d"),
    (110, 1, "mds", "root", "/System/Library/Frameworks/CoreServices.framework/Frameworks/"
     "Metadata.framework/Support/mds", None, 60, (0, 40), 8, ""),
    (118, 1, "fseventsd", "root", "/System/Library/Frameworks/CoreServices.framework/Versions/A/"
     "Frameworks/FSEvents.framework/Support/fseventsd", None, 20, (0, 0), 12, "d"),
    (125, 1, "opendirectoryd", "root", "/usr/libexec/opendirectoryd", None, 25, (0, 5), 7, ""),
    (140, 1, "coreaudiod", "_coreaudiod", "/usr/sbin/coreaudiod", "/usr/sbin/coreaudiod", 25,
     (0, 30), 8, ""),
    (155, 1, "WindowServer", "_windowserver",
     "/System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer",
     "/System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer -daemon",
     600, (30, 250), 22, ""),
    (171, 1, "bluetoothd", "root", "/usr/sbin/bluetoothd", None, 18, (0, 0), 6, "d"),
    (188, 1, "airportd", "root", "/usr/libexec/airportd", None, 22, (0, 10), 7, ""),
    (203, 1, "securityd", "root", "/usr/sbin/securityd", None, 16, (0, 5), 4, ""),
    (240, 1, "syspolicyd", "root", "/usr/libexec/syspolicyd", None, 30, (0, 10), 5, ""),
    (261, 1, "trustd", "root", "/usr/libexec/trustd", None, 20, (0, 5), 3, ""),
    (412, 1, "mds_stores", "root", "/System/Library/Frameworks/CoreServices.framework/"
     "Frameworks/Metadata.framework/Support/mds_stores", None, 180, (0, 60), 7, ""),
    (430, 1, "coreservicesd", "root", "/System/Library/CoreServices/coreservicesd", None, 40,
     (0, 5), 5, ""),
    (470, 1, "cfprefsd", "root", "/usr/sbin/cfprefsd", "/usr/sbin/cfprefsd daemon", 10, (0, 5),
     3, ""),
    (520, 1, "loginwindow", "demo", "/System/Library/CoreServices/loginwindow.app/Contents/MacOS/"
     "loginwindow", "/System/Library/CoreServices/loginwindow.app/Contents/MacOS/loginwindow "
     "console", 60, (0, 5), 4, ""),
    (555, 1, "sharingd", "demo", "/usr/libexec/sharingd", "/usr/libexec/sharingd", 40, (0, 10),
     6, ""),
    (590, 1, "Dock", "demo", "/System/Library/CoreServices/Dock.app/Contents/MacOS/Dock",
     "/System/Library/CoreServices/Dock.app/Contents/MacOS/Dock", 90, (0, 20), 5, ""),
    (600, 1, "SystemUIServer", "demo", "/System/Library/CoreServices/SystemUIServer.app/Contents/"
     "MacOS/SystemUIServer", "/System/Library/CoreServices/SystemUIServer.app/Contents/MacOS/"
     "SystemUIServer", 50, (0, 10), 4, ""),
    (612, 1, "ControlCenter", "demo", "/System/Library/CoreServices/ControlCenter.app/Contents/"
     "MacOS/ControlCenter", "/System/Library/CoreServices/ControlCenter.app/Contents/MacOS/"
     "ControlCenter", 70, (0, 10), 6, ""),
    (630, 1, "Spotlight", "demo", "/System/Library/CoreServices/Spotlight.app/Contents/MacOS/"
     "Spotlight", "/System/Library/CoreServices/Spotlight.app/Contents/MacOS/Spotlight", 80,
     (0, 10), 7, ""),
    (689, 1, "Finder", "demo", "/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder",
     "/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder", 220, (0, 20), 9, ""),
    (700, 1, "photoanalysisd", "demo", "/System/Library/PrivateFrameworks/PhotoAnalysis.framework/"
     "Versions/A/Support/photoanalysisd", "/System/Library/PrivateFrameworks/"
     "PhotoAnalysis.framework/Versions/A/Support/photoanalysisd", 120, (0, 80), 10, ""),
    (742, 1, "Dropbox", "demo", _app("Dropbox"), _app("Dropbox") + " /firstrunupdate 1203", 300,
     (0, 30), 60, ""),
    (760, 1, "1Password", "demo", _app("1Password"), _app("1Password") + " --silent", 250,
     (0, 15), 28, ""),
    (1203, 1, "Docker", "demo", _app("Docker"), _app("Docker"), 120, (0, 20), 18, ""),
    (1219, 1203, "com.docker.backend", "demo", "/Applications/Docker.app/Contents/MacOS/"
     "com.docker.backend", "/Applications/Docker.app/Contents/MacOS/com.docker.backend services",
     260, (5, 40), 25, ""),
    (1250, 1219, "com.docker.vpnkit", "demo", "/Applications/Docker.app/Contents/MacOS/"
     "com.docker.vpnkit", "/Applications/Docker.app/Contents/MacOS/com.docker.vpnkit "
     "--ethernet fd:3", 40, (0, 20), 8, ""),
    (1288, 1219, "qemu-system-aarch64", "demo", "/Applications/Docker.app/Contents/MacOS/"
     "qemu-system-aarch64", "/Applications/Docker.app/Contents/MacOS/qemu-system-aarch64 "
     "-accel hvf -cpu host -machine virt,highmem=on -m 8092 -smp 4", 2400, (60, 200), 14, ""),
    (1984, 1, "Spotify", "demo", _app("Spotify"), _app("Spotify"), 300, (10, 60), 40, ""),
    (1990, 1984, "Spotify Helper (Renderer)", "demo", _app("Spotify", "Spotify Helper (Renderer)"),
     _app("Spotify", "Spotify Helper (Renderer)") + RND + "4", 180, (10, 80), 16, ""),
    (2207, 1, "Mail", "demo", "/System/Applications/Mail.app/Contents/MacOS/Mail",
     "/System/Applications/Mail.app/Contents/MacOS/Mail", 350, (0, 30), 12, ""),
    (2311, 1, "Google Chrome", "demo", CH + "/MacOS/Google Chrome", CH + "/MacOS/Google Chrome",
     250, (20, 100), 45, ""),
    (2330, 2311, "Google Chrome Helper (GPU)", "demo",
     CHH + " (GPU).app/Contents/MacOS/Google Chrome Helper (GPU)",
     CHH + " (GPU).app/Contents/MacOS/Google Chrome Helper (GPU) --type=gpu-process", 350,
     (30, 180), 16, ""),
    (2334, 2311, "Google Chrome Helper", "demo", CHH + ".app/Contents/MacOS/Google Chrome Helper",
     CHH + ".app/Contents/MacOS/Google Chrome Helper --type=utility "
     "--utility-sub-type=network.mojom.NetworkService", 60, (0, 30), 10, ""),
    (2338, 2311, "Google Chrome Helper", "demo", CHH + ".app/Contents/MacOS/Google Chrome Helper",
     CHH + ".app/Contents/MacOS/Google Chrome Helper --type=utility "
     "--utility-sub-type=storage.mojom.StorageService", 40, (0, 10), 8, ""),
    *[_chrome_renderer(pid, 2311, mib, i + 3) for i, (pid, mib) in enumerate(
        [(2350, 420), (2366, 300), (2402, 220), (2417, 180), (2431, 150), (2460, 120),
         (2488, 90), (2512, 70)])],
    (3105, 1, "Notion", "demo", _app("Notion"), _app("Notion"), 200, (5, 40), 30, ""),
    (3110, 3105, "Notion Helper (GPU)", "demo", _app("Notion", "Notion Helper (GPU)"),
     _app("Notion", "Notion Helper (GPU)") + " --type=gpu-process", 120, (5, 60), 14, ""),
    (3121, 3105, "Notion Helper (Renderer)", "demo", _app("Notion", "Notion Helper (Renderer)"),
     _app("Notion", "Notion Helper (Renderer)") + RND + "5", 350, (20, 200), 19, ""),
    (3480, 1, "Slack", "demo", _app("Slack"), _app("Slack"), 250, (5, 50), 32, ""),
    (3490, 3480, "Slack Helper (GPU)", "demo", _app("Slack", "Slack Helper (GPU)"),
     _app("Slack", "Slack Helper (GPU)") + " --type=gpu-process", 110, (5, 60), 13, ""),
    (3502, 3480, "Slack Helper (Renderer)", "demo", _app("Slack", "Slack Helper (Renderer)"),
     _app("Slack", "Slack Helper (Renderer)") + RND + "6", 300, (20, 200), 18, ""),
    (4012, 1, "Code", "demo", "/Applications/Visual Studio Code.app/Contents/MacOS/Electron",
     "/Applications/Visual Studio Code.app/Contents/MacOS/Electron", 180, (5, 40), 33, ""),
    (4031, 4012, "Code Helper (Renderer)", "demo",
     _app("Visual Studio Code", "Code Helper (Renderer)"),
     _app("Visual Studio Code", "Code Helper (Renderer)") + RND + "3", 600, (20, 300), 21, ""),
    (4040, 4012, "Code Helper (Plugin)", "demo", _app("Visual Studio Code", "Code Helper (Plugin)"),
     _app("Visual Studio Code", "Code Helper (Plugin)") + " --type=utility "
     "--utility-sub-type=node.mojom.NodeService", 450, (10, 150), 24, ""),
    (4044, 4012, "Code Helper (GPU)", "demo", _app("Visual Studio Code", "Code Helper (GPU)"),
     _app("Visual Studio Code", "Code Helper (GPU)") + " --type=gpu-process", 100, (5, 50), 12,
     ""),
    (4410, 1, "Terminal", "demo",
     "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal",
     "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal", 120, (0, 30), 8, ""),
    (4420, 4410, "login", "root", "/usr/bin/login", None, 5, (0, 0), 2, ""),
    (4425, 4420, "zsh", "demo", "/bin/zsh", "-zsh", 8, (0, 5), 1, ""),
    (6620, 1, "zoom.us", "demo", _app("zoom.us"), _app("zoom.us"), 450, (60, 300), 50, ""),
    (11842, 4425, "node", "demo", "/opt/homebrew/bin/node",
     "node server.js --port 3000 --api-key sk-demo" + "0123456789abcdefghijklmn",  # fake; redacted
     300, (5, 80), 11, ""),
    (14207, 4425, "python3", "demo", "/opt/homebrew/bin/python3",
     "/opt/homebrew/bin/python3 train.py --epochs 20 --batch-size 64", 620, (300, 900), 9, ""),
]
CWD = {11842: "/Users/demo/src/api", 14207: "/Users/demo/src/model", 4425: "/Users/demo"}
PORTS = {11842: [{"port": 3000, "proto": "tcp", "addr": "127.0.0.1"}],
         1984: [{"port": 57621, "proto": "tcp", "addr": "0.0.0.0"},
                {"port": 57621, "proto": "udp", "addr": "0.0.0.0"}]}

# per moment: system (MiB: wired, active, inactive, compressed, swap_used, swap_total, pressure),
# rss overrides {pid: MiB}, extra processes (new since the last moment: cpu null)
CHROME_TABS = [_chrome_renderer(16100 + i * 3, 2311, 140, 20 + i) for i in range(22)]
ZOMBIES = [(16900 + i, 14207, "python3", "demo", None, None, 0, (0, 0), 0, "z") for i in range(2)]
MOMENTS = [
    ((2250, 6150, 4100, 820, 260, 2048, 1), {}, []),
    ((2450, 7700, 3600, 1230, 410, 2048, 1), {1288: 3800}, []),
    ((2350, 8700, 3100, 1540, 610, 2048, 1), {2330: 600, 2350: 520}, CHROME_TABS),
    ((2660, 9420, 1430, 2660, 2360, 3072, 2), {14207: 2600, 1288: 2700, 4031: 900}, []),
    ((2280, 6300, 3900, 900, 300, 2048, 1), {}, ZOMBIES),
]


def _snapshot(rng, k, sysv, over, extra):
    ts = T1 + k * 3600
    wired, active, inactive, compressed, swap_used, swap_total, pressure = (
        v * MIB if i < 6 else v for i, v in enumerate(sysv))
    b = {"wired": wired, "active": active, "inactive": inactive, "compressed": compressed}
    b["free"] = TOTAL - sum(b.values())
    system = {"total": TOTAL, "used": wired + active + compressed, "available": inactive + b["free"],
              "swap_total": swap_total, "swap_used": swap_used, "pressure": pressure,
              "breakdown": b}
    rows, meta = [], {}
    for pid, ppid, name, user, exe, cmd, mib, (c0, c1), th, flags in PROCS + extra:
        new, zombie, denied = pid >= 16000, "z" in flags, "d" in flags
        start = ts - 60 - (pid % 900) if new else BOOT + pid * 7  # pids grow with time
        rss = None if denied or zombie else round(over.get(pid, mib) * rng.uniform(0.98, 1.02)
                                                  * MIB)
        cpu = None if new or denied or zombie else rng.randint(c0, c1)
        status = "zombie" if zombie else ("running" if cpu and cpu > 300 else "sleeping")
        rows.append({"pid": pid, "ppid": ppid, "start_ts": start, "name": name,
                     "app": app_name(name, exe, "darwin"), "user": user, "cmdline": redact(cmd),
                     "rss": rss, "share": None if rss is None else round(rss / TOTAL, 4),
                     "cpu": cpu, "threads": None if denied or zombie else th + rng.randint(0, 2),
                     "status": status})
        meta[pid] = (exe, flags, denied or zombie)
    rows.sort(key=lambda r: (r["rss"] is None, -(r["rss"] or 0)))
    apps = {}
    for r in rows:
        a = apps.setdefault(r["app"], [0, 0])
        a[0] += r["rss"] or 0
        a[1] += 1
    app_rows = sorted(({"app": n, "rss": v, "share": round(v / TOTAL, 4), "procs": c}
                       for n, (v, c) in apps.items()), key=lambda a: -a["rss"])
    by_pid = {r["pid"]: r for r in rows}
    kids = {}
    for r in sorted(rows, key=lambda r: r["pid"]):
        if r["ppid"] != r["pid"]:
            kids.setdefault(r["ppid"], []).append(
                {"pid": r["pid"], "start_ts": r["start_ts"], "name": r["name"]})
    details = {}
    for r in rows:
        pid = r["pid"]
        exe, flags, nomem = meta[pid]
        full = r["user"] == "demo" and not nomem  # other users / zombies: restricted
        rss = r["rss"]
        par = by_pid.get(r["ppid"]) if r["ppid"] != pid else None
        details[f"{pid}:{r['start_ts']}"] = {
            "pid": pid, "ppid": r["ppid"], "start_ts": r["start_ts"], "name": r["name"],
            "app": r["app"], "user": r["user"], "cmdline": r["cmdline"],
            "exe": None if "z" in flags else exe, "cwd": CWD.get(pid, "/") if full else None,
            "status": r["status"], "rss": rss,
            "uss": int(rss * rng.uniform(0.55, 0.8)) if full else None,
            "vms": None if nomem else 400 * GIB + (rss or 0) * 3,
            "pfaults": None if nomem else rng.randint(10_000, 4_000_000),
            "pageins": None if nomem else rng.randint(0, 5000),
            "cpu_user": None if nomem else rng.randint(1, 9000),
            "cpu_system": None if nomem else rng.randint(1, 3000),
            "threads": r["threads"], "open_files": rng.randint(5, 200) if full else None,
            "ports": PORTS.get(pid, []) if full else None,
            "parent": par and {"pid": par["pid"], "start_ts": par["start_ts"],
                               "name": par["name"]},
            "children": kids.get(pid, []), "restricted": not full}
    return {"ts": ts, "simulated": True, "system": system, "apps": app_rows, "processes": rows,
            "details": details}


def generate(seed: int = 42) -> dict:
    rng = random.Random(seed)
    meta = {"mode": "demo", "host": "demo-mac", "os": "darwin", "total_ram": TOTAL,
            "version": __version__}
    return {"meta": meta, "snapshots": [_snapshot(rng, k, *m) for k, m in enumerate(MOMENTS)]}


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "site" / "sample.json"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w") as f:
        json.dump(generate(42), f, separators=(",", ":"))
    print(f"wrote {out} ({out.stat().st_size} bytes)")
