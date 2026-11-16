"""Deterministic 6-hour synthetic dataset for the public demo (live replay from T0 at 60x).

Run: python scripts/make_demo.py  (writes site/sample.json)
All numbers are ints (exact JSON roundtrip). Times: seconds from dataset start.
Planted events (sim time, relative to T0 = 4 h): steady grower T0-28 min -> T0+2 min,
fast grower T0-4.5 min -> T0+30 s, big newcomer at T0+1 min, swap growth T0+20 -> 30 min,
memory pressure T0+40 -> 50 min. Everything else stays below the insight thresholds.
"""
import json
import math
import random
from pathlib import Path

from rms import __version__
from rms.redact import redact

MIB, GIB = 2**20, 2**30
TOTAL, STEP = 16 * GIB, 10
N = 6 * 3600 // STEP
END = (N - 1) * STEP
T0 = 4 * 3600
BASELINE, OVERLAP = int(2.5 * GIB), 0.85
PHI = 0.97  # AR(1) mean reversion per sample
SWAP0, SWAP_ADD = 300 * MIB, 800 * MIB
PRESSURE = (T0 + 40 * 60, T0 + 50 * 60)

CH = "/Applications/Google Chrome.app/Contents"
CHH = CH + "/Frameworks/Google Chrome Framework.framework/Helpers"
EL = "--type=renderer --lang=en-US --num-raster-threads=4 --renderer-client-id="
# pid, ppid, name, app, user, cmdline, MiB, cpu range (percent x 10), threads, life (start, end) or None
PROCS = [
    (0, 0, "kernel_task", "kernel_task", "root", "kernel_task", 350, (20, 120), 240, None),
    (1, 0, "launchd", "launchd", "root", "/sbin/launchd", 20, (0, 10), 4, None),
    (155, 1, "WindowServer", "WindowServer", "_windowserver",
     "/System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer -daemon",
     600, (30, 250), 22, None),
    (412, 1, "mds_stores", "mds_stores", None, None, 180, (0, 60), 7, None),
    (689, 1, "Finder", "Finder", "demo",
     "/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder", 220, (0, 20), 9, None),
    (742, 1, "Dropbox", "Dropbox", "demo",
     "/Applications/Dropbox.app/Contents/MacOS/Dropbox /firstrunupdate 1203", 300, (0, 30), 60,
     None),
    (760, 1, "1Password", "1Password", "demo",
     "/Applications/1Password.app/Contents/MacOS/1Password --silent", 250, (0, 15), 28, None),
    (1203, 1, "Docker", "Docker", "demo",
     "/Applications/Docker.app/Contents/MacOS/Docker", 120, (0, 20), 18, None),
    (1219, 1203, "com.docker.backend", "Docker", "demo",
     "/Applications/Docker.app/Contents/MacOS/com.docker.backend services", 260, (5, 40), 25,
     None),
    (1288, 1219, "qemu-system-aarch64", "Docker", "demo",
     "/Applications/Docker.app/Contents/MacOS/qemu-system-aarch64 -accel hvf -cpu host"
     " -machine virt,highmem=on -m 8092 -smp 4 -drive if=virtio,format=raw,"
     "file=/Users/demo/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw",
     2400, (60, 200), 14, None),
    (1984, 1, "Spotify", "Spotify", "demo",
     "/Applications/Spotify.app/Contents/MacOS/Spotify", 300, (10, 60), 40, None),
    (2207, 1, "Mail", "Mail", "demo",
     "/System/Applications/Mail.app/Contents/MacOS/Mail", 350, (0, 30), 12, None),
    (2311, 1, "Google Chrome", "Google Chrome", "demo",
     CH + "/MacOS/Google Chrome", 250, (20, 100), 45, None),
    (2330, 2311, "Google Chrome Helper (GPU)", "Google Chrome", "demo",
     CHH + "/Google Chrome Helper (GPU).app/Contents/MacOS/Google Chrome Helper (GPU)"
     " --type=gpu-process --lang=en-US", 300, (30, 180), 16, None),
    (2350, 2311, "Google Chrome Helper (Renderer)", "Google Chrome", "demo",
     CHH + "/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) "
     + EL + "7", 420, (50, 400), 20, None),
    (2366, 2311, "Google Chrome Helper (Renderer)", "Google Chrome", "demo",
     CHH + "/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) "
     + EL + "12", 300, (50, 300), 18, None),
    (2402, 2311, "Google Chrome Helper (Renderer)", "Google Chrome", "demo",
     CHH + "/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) "
     + EL + "19", 220, (50, 250), 17, None),
    (3105, 1, "Notion", "Notion", "demo",
     "/Applications/Notion.app/Contents/MacOS/Notion", 200, (5, 40), 30, None),
    (3121, 3105, "Notion Helper (Renderer)", "Notion", "demo",
     "/Applications/Notion.app/Contents/Frameworks/Notion Helper (Renderer).app/Contents/MacOS/"
     "Notion Helper (Renderer) " + EL + "5", 350, (50, 250), 19, None),
    (3480, 1, "Slack", "Slack", "demo",
     "/Applications/Slack.app/Contents/MacOS/Slack", 250, (5, 50), 32, None),
    (3502, 3480, "Slack Helper (Renderer)", "Slack", "demo",
     "/Applications/Slack.app/Contents/Frameworks/Slack Helper (Renderer).app/Contents/MacOS/"
     "Slack Helper (Renderer) " + EL + "6", 300, (50, 200), 18, None),
    (4012, 1, "Code", "Visual Studio Code", "demo",
     "/Applications/Visual Studio Code.app/Contents/MacOS/Electron", 180, (5, 40), 33, None),
    (4031, 4012, "Code Helper (Renderer)", "Visual Studio Code", "demo",
     "/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Renderer).app/"
     "Contents/MacOS/Code Helper (Renderer) " + EL + "3", 600, (50, 300), 21, None),
    (4410, 1, "Terminal", "Terminal", "demo",
     "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal", 120, (0, 30), 8, None),
    (6620, 1, "zoom.us", "zoom.us", "demo",
     "/Applications/zoom.us.app/Contents/MacOS/zoom.us", 450, (100, 400), 50, (3600, 3 * 3600)),
    (11842, 4410, "node", "node", "demo",
     "node server.js --port 3000 --api-key sk-demo" + "0123456789abcdefghijklmn",  # fake; redacted
     300, (5, 80), 11, (2 * 3600, None)),
    (14207, 4410, "python3", "python3", "demo",
     "/opt/homebrew/bin/python3 train.py --epochs 20 --batch-size 64", 620, (300, 900), 9,
     (T0 + 60, None)),  # big newcomer
]


def _ramp(t, start, secs):
    return min(1.0, max(0.0, (t - start) / secs))


def _planted(pid, t):
    """Extra MiB on top of a process's level at sim time t."""
    if pid == 3121:  # steady grower: +500 MiB, linear over T0-28 min -> T0+2 min, then holds
        return 500 * _ramp(t, T0 - 28 * 60, 30 * 60)
    if pid == 3502:  # fast grower: +400 MiB over T0-4.5 min -> T0+30 s, frees it at T0+4 min
        return 400 * (_ramp(t, T0 - 270, 300) - _ramp(t, T0 + 240, 60))
    return 0


def generate(seed: int = 42) -> dict:
    rng = random.Random(seed)
    sd = [max(1.0, p[6] / 100) for p in PROCS]  # jitter sd ~1% of level, MiB
    ar = [rng.gauss(0, s) for s in sd]
    procs = {f"p{k + 1}": {"pid": pid, "ppid": ppid, "name": name, "app": app, "user": user,
                           "cmdline": cmd and redact(cmd), "start_ts": None,
                           "rss": [], "cpu": [], "threads": []}
             for k, (pid, ppid, name, app, user, cmd, *_r) in enumerate(PROCS)}
    rows = list(procs.values())
    sys_ = {k: [] for k in ("ts", "used", "available", "swap_used", "pressure")}
    for i in range(N):
        t = i * STEP
        total = 0
        for k, (pid, _, _, _, _, _, mib, (c0, c1), th, life) in enumerate(PROCS):
            ar[k] = PHI * ar[k] + rng.gauss(0, sd[k] * math.sqrt(1 - PHI**2))
            row = rows[k]
            if life and not (life[0] <= t and (life[1] is None or t < life[1])):
                for f in ("rss", "cpu", "threads"):
                    row[f].append(None)
                continue
            rss = round((mib + ar[k] + _planted(pid, t)) * MIB)
            row["rss"].append(rss)
            row["cpu"].append(rng.randint(c0, c1))
            row["threads"].append(th + rng.randint(0, 2))
            if row["start_ts"] is None:
                row["start_ts"] = t
            total += rss
        hot = PRESSURE[0] <= t < PRESSURE[1]
        used = (int(0.93 * TOTAL) + rng.randint(0, 64 * MIB) if hot
                else min(int(0.95 * TOTAL), int(OVERLAP * total) + BASELINE))
        sys_["ts"].append(t)
        sys_["used"].append(used)
        sys_["available"].append(TOTAL - used)
        sys_["swap_used"].append(SWAP0 + round(SWAP_ADD * _ramp(t, T0 + 20 * 60, 10 * 60)))
        sys_["pressure"].append(2 if hot else 1)
    meta = {"mode": "demo", "host": "demo-mac", "os": "darwin", "total_ram": TOTAL,
            "interval": STEP, "first_ts": 0, "last_ts": END, "version": __version__,
            "relative": True, "t0": T0}
    return {"meta": meta, "system": sys_, "processes": procs}


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "site" / "sample.json"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w") as f:
        json.dump(generate(42), f, separators=(",", ":"))
    print(f"wrote {out}")
