// Data sources for the dashboard. LiveSource talks to the local server; DemoSource serves the
// five snapshots in sample.json with the same /api/live and /api/process shapes.

const PERIOD = 6; // seconds per demo snapshot
const JITTER = 0.019; // ponytail: just under the ±2% bound so rounding never crosses it

// Deterministic factor in [1 - JITTER, 1 + JITTER] per (cycle, pid): same data within a window.
function factor(cycle, pid) {
  let h = Math.imul(cycle ^ 0x9e3779b9, 0x85ebca6b) ^ Math.imul(pid, 0xc2b2ae35);
  h = Math.imul(h ^ (h >>> 15), 0x2c1b3c6d);
  h ^= h >>> 13;
  return 1 + JITTER * ((h >>> 0) / 0xffffffff * 2 - 1);
}
const share = (rss, total) => (rss == null ? null : Math.round((rss / total) * 1e4) / 1e4);

// Static demo: snapshot index = 1 + floor(elapsed / 6 s), wrapping over the 5 snapshots.
export class DemoSource {
  constructor(dataset, { clock = () => Date.now() / 1000 } = {}) {
    this.data = dataset;
    this.clock = clock;
    this.start = clock();
  }

  async meta() { return { ...this.data.meta, features: ["now"] }; }

  _now() {
    const cycle = Math.floor((this.clock() - this.start) / PERIOD), n = this.data.snapshots.length;
    return { cycle, snap: this.data.snapshots[(((1 + cycle) % n) + n) % n] };
  }

  async live() {
    const { cycle, snap } = this._now();
    const { details, ...body } = structuredClone(snap);
    const total = body.system.total;
    for (const p of body.processes) {
      const f = factor(cycle, p.pid);
      if (p.rss != null) p.rss = Math.round(p.rss * f);
      if (p.cpu != null) p.cpu = Math.round(p.cpu * f);
      p.share = share(p.rss, total);
    }
    for (const a of body.apps) {
      a.rss = body.processes.reduce((s, p) => s + (p.app === a.app ? p.rss ?? 0 : 0), 0);
      a.share = share(a.rss, total);
    }
    return { ...body, simulated: true };
  }

  async process(pid, start_ts) {
    const { cycle, snap } = this._now();
    const d = snap.details[`${pid}:${start_ts}`];
    if (!d) throw Object.assign(new Error("not found"), { status: 404 });
    const out = structuredClone(d);
    if (out.rss != null) out.rss = Math.round(out.rss * factor(cycle, pid));
    return out;
  }
}

// Local server API.
export class LiveSource {
  async _get(path, params = {}) {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) if (v != null) q.set(k, v);
    const qs = String(q);
    const r = await fetch(qs ? `/api/${path}?${qs}` : `/api/${path}`);
    const body = await r.json();
    if (!r.ok) throw Object.assign(new Error(body.error || `HTTP ${r.status}`), { status: r.status });
    return body;
  }

  meta() { return this._get("meta"); }
  live() { return this._get("live"); }
  process(pid, start_ts) { return this._get("process", { pid, start_ts }); }
}

// True when the server predates now-only snapshots: version < 0.4.0 or no "now" feature.
// Segments compare by their leading digits, so "0.4.0rc1" counts as 0.4.0.
export function outdated(meta) {
  if (!meta?.features?.includes("now")) return true;
  const v = String(meta.version ?? "").split(".").map((x) => parseInt(x, 10));
  if (v.length < 2 || v.some(isNaN)) return true;
  for (const [i, min] of [0, 4, 0].entries()) if ((v[i] ?? 0) !== min) return (v[i] ?? 0) < min;
  return false;
}

// Live server if /api/meta answers within 1.5 s, else the static demo dataset. On localhost
// any 200 counts, so an older server still gets LiveSource and app.js can ask for an upgrade.
export async function loadSource() {
  const local = typeof location !== "undefined" && ["127.0.0.1", "localhost"].includes(location.hostname);
  try {
    const r = await fetch("/api/meta", { signal: AbortSignal.timeout(1500) });
    if (r.ok && (local || (await r.json()).mode === "live")) return new LiveSource();
  } catch { /* no server answered */ }
  if (local) throw Object.assign(new Error("RMS server not responding"), { local: true });
  const r = await fetch("./sample.json");
  if (!r.ok) throw new Error(`sample.json: HTTP ${r.status}`);
  return new DemoSource(await r.json());
}
