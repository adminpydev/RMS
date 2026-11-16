"""Deterministic 3-day synthetic dataset for the public demo.

Run: python scripts/make_demo.py  (writes site/sample.json)
All numbers are ints (exact JSON roundtrip). Times: seconds from day-1 00:00.
"""
import json
import math
import random
from pathlib import Path

from rms import __version__
from rms.redact import redact

MIB, GIB = 2**20, 2**30
TOTAL, STEP, DAYS = 16 * GIB, 30, 3
N = DAYS * 86400 // STEP
BASELINE, OVERLAP = int(2.5 * GIB), 0.85
PHI = 0.97  # AR(1) mean reversion per sample

# name, MiB while idle (0 = not running), MiB in session, open min, close min, procs, jitter sd MiB
# Opens spaced 20 min apart, each step <= 700 MiB, one open + one close per day.
APPS = [
    ("WindowServer", 600, 600, None, None, 2, 4),
    ("kernel_task", 350, 350, None, None, 1, 3),
    ("Finder", 220, 220, None, None, 2, 4),
    ("Docker", 1400, 1400, None, None, 6, 12),
    ("Other (small apps)", 3200, 3900, 530, 1130, 60, 15),
    ("Google Chrome", 0, 700, 550, 1110, 14, 15),
    ("Notion", 0, 600, 570, 1090, 5, 8),
    ("Slack", 0, 600, 590, 1070, 6, 12),
    ("Visual Studio Code", 0, 700, 610, 1050, 9, 15),
    ("Mail", 0, 400, 510, 1020, 2, 8),
    ("Spotify", 0, 450, 490, 1150, 5, 8),
    ("Terminal", 0, 200, 630, 1030, 2, 6),
    ("zoom.us", 0, 500, 790, 935, 4, 10),
]
END = (N - 1) * STEP
CH = "/Applications/Google Chrome.app/Contents"
CHH = CH + "/Frameworks/Google Chrome Framework.framework/Helpers"
EL = "--type=renderer --lang=en-US --num-raster-threads=4 --renderer-client-id="
# pid, ppid, name, app (process), group (APPS row it sums into), user, cmdline,
# share of the group's level, cpu range (percent x 10), own life (start ts, ramp s, MiB) or None.
# Shares per group sum to 1, so group rss = sum of its processes = the v0.1 app series.
PROCS = [
    (0, 0, "kernel_task", "kernel_task", "kernel_task", "root", "kernel_task", 1, (20, 120), None),
    (1, 0, "launchd", "launchd", "Other (small apps)", "root", "/sbin/launchd", .01, (0, 10), None),
    (155, 1, "WindowServer", "WindowServer", "WindowServer", "_windowserver",
     "/System/Library/PrivateFrameworks/SkyLight.framework/Resources/WindowServer -daemon", 1,
     (30, 250), None),
    (412, 1, "mds_stores", "mds_stores", "Other (small apps)", None, None, .09, (0, 60), None),
    (689, 1, "Finder", "Finder", "Finder", "demo",
     "/System/Library/CoreServices/Finder.app/Contents/MacOS/Finder", 1, (0, 20), None),
    (742, 1, "Dropbox", "Dropbox", "Other (small apps)", "demo",
     "/Applications/Dropbox.app/Contents/MacOS/Dropbox /firstrunupdate 1203", .3, (0, 30), None),
    (760, 1, "1Password", "1Password", "Other (small apps)", "demo",
     "/Applications/1Password.app/Contents/MacOS/1Password --silent", .6, (0, 15), None),
    (1203, 1, "Docker", "Docker", "Docker", "demo",
     "/Applications/Docker.app/Contents/MacOS/Docker", .1, (0, 20), None),
    (1219, 1203, "com.docker.backend", "Docker", "Docker", "demo",
     "/Applications/Docker.app/Contents/MacOS/com.docker.backend services", .15, (5, 40), None),
    (1288, 1219, "qemu-system-aarch64", "Docker", "Docker", "demo",
     "/Applications/Docker.app/Contents/MacOS/qemu-system-aarch64 -accel hvf -cpu host"
     " -machine virt,highmem=on -m 8092 -smp 4 -drive if=virtio,format=raw,"
     "file=/Users/demo/Library/Containers/com.docker.docker/Data/vms/0/data/Docker.raw",
     .75, (60, 200), None),
    (1984, 1, "Spotify", "Spotify", "Spotify", "demo",
     "/Applications/Spotify.app/Contents/MacOS/Spotify", 1, (10, 60), None),
    (2207, 1, "Mail", "Mail", "Mail", "demo",
     "/System/Applications/Mail.app/Contents/MacOS/Mail", 1, (0, 30), None),
    (2311, 1, "Google Chrome", "Google Chrome", "Google Chrome", "demo",
     CH + "/MacOS/Google Chrome", .3, (20, 100), None),
    (2330, 2311, "Google Chrome Helper (GPU)", "Google Chrome", "Google Chrome", "demo",
     CHH + "/Google Chrome Helper (GPU).app/Contents/MacOS/Google Chrome Helper (GPU)"
     " --type=gpu-process --lang=en-US", .15, (30, 180), None),
    (2350, 2311, "Google Chrome Helper (Renderer)", "Google Chrome", "Google Chrome", "demo",
     CHH + "/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) "
     + EL + "7", .2, (50, 400), None),
    (2366, 2311, "Google Chrome Helper (Renderer)", "Google Chrome", "Google Chrome", "demo",
     CHH + "/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) "
     + EL + "12", .2, (50, 300), None),
    (2402, 2311, "Google Chrome Helper (Renderer)", "Google Chrome", "Google Chrome", "demo",
     CHH + "/Google Chrome Helper (Renderer).app/Contents/MacOS/Google Chrome Helper (Renderer) "
     + EL + "19", .15, (50, 250), None),
    (3105, 1, "Notion", "Notion", "Notion", "demo",
     "/Applications/Notion.app/Contents/MacOS/Notion", .4, (5, 40), None),
    (3121, 3105, "Notion Helper (Renderer)", "Notion", "Notion", "demo",
     "/Applications/Notion.app/Contents/Frameworks/Notion Helper (Renderer).app/Contents/MacOS/"
     "Notion Helper (Renderer) " + EL + "5", .6, (50, 250), None),
    (3480, 1, "Slack", "Slack", "Slack", "demo",
     "/Applications/Slack.app/Contents/MacOS/Slack", .45, (5, 50), None),
    (3502, 3480, "Slack Helper (Renderer)", "Slack", "Slack", "demo",
     "/Applications/Slack.app/Contents/Frameworks/Slack Helper (Renderer).app/Contents/MacOS/"
     "Slack Helper (Renderer) " + EL + "6", .55, (50, 200), None),
    (4012, 1, "Code", "Visual Studio Code", "Visual Studio Code", "demo",
     "/Applications/Visual Studio Code.app/Contents/MacOS/Electron", .25, (5, 40), None),
    (4031, 4012, "Code Helper (Renderer)", "Visual Studio Code", "Visual Studio Code", "demo",
     "/Applications/Visual Studio Code.app/Contents/Frameworks/Code Helper (Renderer).app/"
     "Contents/MacOS/Code Helper (Renderer) " + EL + "3", .75, (50, 300), None),
    (4410, 1, "Terminal", "Terminal", "Terminal", "demo",
     "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal", 1, (0, 30), None),
    (6620, 1, "zoom.us", "zoom.us", "zoom.us", "demo",
     "/Applications/zoom.us.app/Contents/MacOS/zoom.us", 1, (100, 400), None),
    # newcomers: dev server from 15:00 on day 3; training script in the last 8 min (fast grower)
    (11842, 1, "node", "node", "Other (small apps)", "demo",
     "node server.js --port 3000 --api-key sk-demo" + "0123456789abcdefghijklmn",  # fake; redacted
     0, (5, 80), (2 * 86400 + 15 * 3600, 120, 550)),
    (14207, 1, "python3", "python3", "Other (small apps)", "demo",
     "/opt/homebrew/bin/python3 train.py --epochs 20 --batch-size 64", 0, (300, 900),
     (END - 480, 480, 720)),
]
# pid, day, start sec, end sec, MiB: rise in 3 min, hold, step back to baseline
SPIKES = [
    (1288, 0, 14 * 3600, 14 * 3600 + 45 * 60, 3072),  # Docker qemu
    (2402, 2, 11 * 3600, 11 * 3600 + 30 * 60, 2560),  # Chrome renderer
]
LEAK = (3121, 1, 10 * 3600, 16 * 3600, 150)  # Notion renderer, day 2 10:00-16:00, MiB/h; quits 16:00
LEAK_APP = "Notion"
PSD = 2  # own jitter sd per process, MiB


def generate(seed: int = 42) -> dict:
    rng = random.Random(seed)
    sched = {a[0]: [(rng.randint(-2, 2), rng.randint(-2, 2)) for _ in range(DAYS)] for a in APPS}
    ar = {a[0]: rng.gauss(0, a[6]) for a in APPS}
    par = [rng.gauss(0, PSD) for _ in PROCS]
    sys_ = {k: [] for k in ("ts", "used", "available", "cached", "compressed", "swap_used", "pressure")}
    apps = {a[0]: {"rss": [], "procs": []} for a in APPS}
    procs = {f"p{k + 1}": {"pid": pid, "ppid": ppid, "name": name, "app": app, "user": user,
                           "cmdline": redact(cmd), "start_ts": None, "rss": [], "cpu": []}
             for k, (pid, ppid, name, app, _, user, cmd, *_r) in enumerate(PROCS)}
    rows = list(procs.values())
    for i in range(N):
        t = i * STEP
        day, sec = divmod(t, 86400)
        level = {}
        for name, lo, hi, op, cl, n_procs, sd in APPS:
            ar[name] = PHI * ar[name] + rng.gauss(0, sd * math.sqrt(1 - PHI**2))
            lv = lo or None
            if op is not None:
                jo, jc = sched[name][day]
                end = LEAK[3] if (name, day) == (LEAK_APP, LEAK[1]) else (cl + jc) * 60
                if (op + jo) * 60 <= sec < end:
                    lv = hi
            level[name] = lv
        app_rss = {}
        for k, (pid, _, _, _, group, _, _, share, (c0, c1), life) in enumerate(PROCS):
            par[k] = PHI * par[k] + rng.gauss(0, PSD * math.sqrt(1 - PHI**2))
            lv, row = level[group], rows[k]
            if life:
                lv = None if t < life[0] else life[2] * min(1, (t - life[0]) / life[1])
            if lv is None:
                row["rss"].append(None)
                row["cpu"].append(None)
                continue
            mib = (lv if life else share * (lv + ar[group])) + par[k]
            if pid == LEAK[0] and day == LEAK[1] and sec >= LEAK[2]:
                mib += LEAK[4] * (sec - LEAK[2]) / 3600
            hot = False
            for sp, sd_, s0, s1, add in SPIKES:
                if sp == pid and sd_ == day and s0 <= sec < s1:
                    mib += add * min(1, (sec - s0) / 180)
                    hot = True
            rss = max(MIB, round(mib * MIB))
            row["rss"].append(rss)
            row["cpu"].append(rng.randint(400, 1500) if hot else rng.randint(c0, c1))
            if row["start_ts"] is None:
                row["start_ts"] = t
            app_rss[group] = app_rss.get(group, 0) + rss
        total = sum(app_rss.values())
        for name, *_r, n_procs, sd in APPS:
            r = app_rss.get(name)
            apps[name]["rss"].append(r)
            apps[name]["procs"].append(None if r is None else max(1, n_procs + round(ar[name] / sd)))
        used = min(int(0.95 * TOTAL), int(OVERLAP * total) + BASELINE)
        hot = used > 0.85 * TOTAL
        sys_["ts"].append(t)
        sys_["used"].append(used)
        sys_["available"].append(TOTAL - used)
        sys_["cached"].append(None)
        sys_["compressed"].append(round(used * (0.2 if hot else 0.06)))
        sys_["swap_used"].append(512 * MIB if hot else 0)
        sys_["pressure"].append(2 if hot else 1)
    meta = {"mode": "demo", "host": "demo-mac", "os": "darwin", "total_ram": TOTAL,
            "interval": STEP, "first_ts": 0, "last_ts": END, "version": __version__,
            "relative": True}
    return {"meta": meta, "system": sys_, "apps": apps, "processes": procs}


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "site" / "sample.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(generate(42), separators=(",", ":")))
    print(f"wrote {out}")
