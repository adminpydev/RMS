"""Deterministic 3-day synthetic dataset for the public demo.

Run: python scripts/make_demo.py  (writes site/sample.json)
All numbers are ints (exact JSON roundtrip). Times: seconds from day-1 00:00.
"""
import json
import math
import random
from pathlib import Path

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
# name, day, start sec, end sec, MiB: rise in 3 min, hold, step back to baseline
SPIKES = [
    ("Docker", 0, 14 * 3600, 14 * 3600 + 45 * 60, 3072),
    ("Google Chrome", 2, 11 * 3600, 11 * 3600 + 30 * 60, 2560),
]
LEAK = ("Notion", 1, 10 * 3600, 16 * 3600, 150)  # day 2 10:00-16:00, MiB/h, app quits at 16:00


def generate(seed: int = 42) -> dict:
    rng = random.Random(seed)
    sched = {a[0]: [(rng.randint(-2, 2), rng.randint(-2, 2)) for _ in range(DAYS)] for a in APPS}
    ar = {a[0]: rng.gauss(0, a[6]) for a in APPS}
    sys_ = {k: [] for k in ("ts", "used", "available", "cached", "compressed", "swap_used", "pressure")}
    apps = {a[0]: {"rss": [], "procs": []} for a in APPS}
    for i in range(N):
        t = i * STEP
        day, sec = divmod(t, 86400)
        total = 0
        for name, lo, hi, op, cl, procs, sd in APPS:
            ar[name] = PHI * ar[name] + rng.gauss(0, sd * math.sqrt(1 - PHI**2))
            level = lo or None
            if op is not None:
                jo, jc = sched[name][day]
                end = LEAK[3] if (name, day) == LEAK[:2] else (cl + jc) * 60
                if (op + jo) * 60 <= sec < end:
                    level = hi
                    if (name, day) == LEAK[:2] and sec >= LEAK[2]:
                        level += LEAK[4] * (sec - LEAK[2]) / 3600
            for sn, sd_, s0, s1, mib in SPIKES:
                if sn == name and sd_ == day and level is not None and s0 <= sec < s1:
                    level += mib * min(1, (sec - s0) / 180)
            if level is None:
                apps[name]["rss"].append(None)
                apps[name]["procs"].append(None)
                continue
            rss = max(MIB, round((level + ar[name]) * MIB))
            total += rss
            apps[name]["rss"].append(rss)
            apps[name]["procs"].append(max(1, procs + round(ar[name] / sd)))
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
            "interval": STEP, "first_ts": 0, "last_ts": (N - 1) * STEP, "version": "0.1.0",
            "relative": True}
    return {"meta": meta, "system": sys_, "apps": apps}


if __name__ == "__main__":
    out = Path(__file__).resolve().parent.parent / "site" / "sample.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(generate(42), separators=(",", ":")))
    print(f"wrote {out}")
