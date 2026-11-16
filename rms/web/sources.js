// Data sources for the dashboard. LiveSource talks to the local server; DemoSource replays
// the static sample.json with the same /api/live and /api/process shapes and definitions
// as rms/live.py, so the UI and insights run identically on both.

const MiB = 2 ** 20;
const WINDOW = 1800; // seconds of history, like Recent(window=1800)

// Static demo: a virtual clock replays the dataset from T0 (meta.t0) at 60x real time and
// wraps back to T0 at the end. Relative ts are shifted so the replay's virtual "now" at
// construction equals the real time then.
export class DemoSource {
  constructor(dataset, { clock = () => Date.now() / 1000 } = {}) {
    const m = dataset.meta;
    this.clock = clock;
    this.start = clock();
    this.t0 = m.t0 ?? m.first_ts;
    this.off = m.relative ? Math.floor(this.start) - this.t0 : 0;
    this.data = dataset;
    this.procs = Object.values(dataset.processes ?? {}).map((p) => ({ ...p, start_ts: p.start_ts + this.off }));
    this.info = { mode: "demo", host: m.host, os: m.os, total_ram: m.total_ram, interval: m.interval,
                  version: m.version, features: ["live"] };
  }

  async meta() { return { ...this.info, features: [...this.info.features] }; }

  // Index of the current sample (positive modulo: clock skew stays in range).
  _now() {
    const { first_ts, last_ts, interval } = this.data.meta;
    const P = last_ts - this.t0 + interval, x = 60 * (this.clock() - this.start);
    const t = this.t0 + Math.floor(((x % P) + P) % P);
    return Math.min(Math.floor((t - first_ts) / interval), this.data.system.ts.length - 1);
  }

  // Window [lo, i] of sample indexes in the last WINDOW s, and the closest-within-tol lookup.
  _ctx(i) {
    const ts = this.data.system.ts, { first_ts, interval } = this.data.meta;
    let lo = i;
    while (lo > 0 && ts[lo - 1] >= ts[i] - WINDOW) lo--;
    // ponytail: samples are regular in the shipped dataset; a binary search if gaps appear
    const near = (target, tol) => {
      const j = Math.min(i, Math.max(lo, Math.round((target - first_ts) / interval)));
      return Math.abs(ts[j] - target) <= tol ? j : null;
    };
    return { ts, lo, near };
  }

  // Simulated /api/live (same shape and definitions as Recent.snapshot).
  async live() {
    const i = this._now(), { ts, lo, near } = this._ctx(i), sys = this.data.system;
    const j5 = near(ts[i] - 300, 30), j30 = near(ts[i] - 1800, 90), j10 = near(ts[i] - 600, 60);
    const alive = this.procs.filter((p) => p.rss[i] != null);
    const diff = (p, j) => (j != null && p.rss[j] != null ? p.rss[i] - p.rss[j] : null);
    const g5 = new Map(alive.map((p) => [p, diff(p, j5)]));
    const top = new Set([...alive].sort((a, b) => b.rss[i] - a.rss[i]).slice(0, 50));
    alive.filter((p) => g5.get(p) > 0).sort((a, b) => g5.get(b) - g5.get(a)).slice(0, 20).forEach((p) => top.add(p));
    const spark = new Map(), minutes = new Map(); // newest sample per 20 s / 60 s bucket
    for (let k = lo; k <= i; k++) {
      const b = Math.floor((ts[i] - ts[k]) / 20);
      if (b < 30) spark.set(b, k);
      minutes.set(Math.floor((ts[i] - ts[k]) / 60), k);
    }
    const sparkIdx = [...spark.keys()].sort((a, b) => b - a).map((b) => spark.get(b));
    const processes = alive.map((p) => {
      const g30 = diff(p, j30);
      let trend = null;
      if (g30 > 200 * MiB) {
        const r2 = fitR2([...minutes.values()].filter((k) => p.rss[k] != null).map((k) => [ts[k], p.rss[k]]));
        trend = r2 == null ? null : { r2 };
      }
      return { pid: p.pid, ppid: p.ppid, start_ts: p.start_ts, name: p.name, app: p.app, user: p.user,
               cmdline: p.cmdline, rss: p.rss[i], cpu: p.cpu?.[i] ?? null, threads: p.threads?.[i] ?? null,
               growth_5m: g5.get(p), growth_30m: g30, trend,
               spark: top.has(p) ? sparkIdx.filter((k) => p.rss[k] != null).map((k) => p.rss[k]) : null };
    });
    return { ts: ts[i] + this.off, resolution: 2, simulated: true,
             system: { total: this.info.total_ram, used: sys.used[i], available: sys.available[i],
                       swap_used: sys.swap_used[i], swap_used_10m_ago: j10 == null ? null : sys.swap_used[j10],
                       pressure: sys.pressure[i] },
             processes };
  }

  // Same shape and parent/children rules as Recent.process; a key not in the window throws like a 404.
  async process(pid, start_ts) {
    const i = this._now(), { ts, lo } = this._ctx(i);
    const d = this.procs.find((p) => p.pid === pid && p.start_ts === start_ts);
    const series = [];
    for (let k = lo; d && k <= i; k++) {
      if (d.rss[k] != null) series.push({ ts: ts[k] + this.off, rss: d.rss[k], cpu: d.cpu?.[k] ?? null });
    }
    if (!series.length) throw new Error("not found");
    const alive = this.procs.filter((p) => p.rss[i] != null && p !== d);
    const par = alive.filter((p) => p.pid === d.ppid && p.start_ts <= start_ts)
      .sort((a, b) => b.start_ts - a.start_ts)[0];
    const children = alive.filter((p) => p.ppid === pid && p.start_ts >= start_ts)
      .sort((a, b) => a.start_ts - b.start_ts || a.pid - b.pid)
      .map((p) => ({ pid: p.pid, start_ts: p.start_ts, name: p.name }));
    return { pid, ppid: d.ppid, start_ts, name: d.name, app: d.app, user: d.user, cmdline: d.cmdline,
             alive: d.rss[i] != null, parent: par ? { pid: par.pid, name: par.name } : null, children, series };
  }
}

// R² of the least-squares line through [ts, rss] points; null if < 20 points or flat (like live._r2).
function fitR2(pts) {
  const n = pts.length;
  if (n < 20) return null;
  const mx = pts.reduce((s, [x]) => s + x, 0) / n, my = pts.reduce((s, [, y]) => s + y, 0) / n;
  let sxx = 0, sxy = 0, sst = 0;
  for (const [x, y] of pts) { sxx += (x - mx) ** 2; sxy += (x - mx) * (y - my); sst += (y - my) ** 2; }
  if (!sst || !sxx) return null;
  const slope = sxy / sxx;
  const ssr = pts.reduce((s, [x, y]) => s + (y - my - slope * (x - mx)) ** 2, 0);
  return 1 - ssr / sst;
}

// Local server API.
export class LiveSource {
  async _get(path, params = {}) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v != null) q.set(k, v);
    const r = await fetch(`/api/${path}?${q}`);
    const body = await r.json();
    if (!r.ok) throw new Error(body.error || `HTTP ${r.status}`);
    return body;
  }

  meta() { return this._get("meta"); }
  live({ fast } = {}) { return this._get("live", { fast: fast ? 1 : null }); }
  process(pid, start_ts) { return this._get("process", { pid, start_ts }); }
}

// Live server if /api/meta answers within 1.5 s, else the static demo dataset.
export async function loadSource() {
  try {
    const r = await fetch("/api/meta", { signal: AbortSignal.timeout(1500) });
    if (r.ok && (await r.json()).mode === "live") return new LiveSource();
  } catch { /* no server answered */ }
  if (typeof location !== "undefined" && ["127.0.0.1", "localhost"].includes(location.hostname)) {
    throw Object.assign(new Error("RMS server not responding"), { local: true });
  }
  const r = await fetch("./sample.json");
  if (!r.ok) throw new Error(`sample.json: HTTP ${r.status}`);
  return new DemoSource(await r.json());
}
