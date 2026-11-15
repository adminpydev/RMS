// Data sources for the dashboard. LiveSource talks to the local server; DemoSource
// serves the static sample.json with the same bucketing rules as rms/store.py, so all
// analytics run identically on both. All `to` bounds are EXCLUSIVE (like the store):
// for a live range ending "now", pass `to = now + 1`.

export const OTHER_SMALL = "Other (small apps)";
const SYS_COLS = ["used", "available", "cached", "compressed", "swap_used", "pressure"];
const MAX_POINTS = 1000; // charts target
const MAX_EXPLICIT_POINTS = 5000; // explicit step clamp, same as rms/server.py

const ceilDiv = (a, b) => Math.ceil(a / b);

// Same as store.pick_step.
function pickStep(from, to, interval) {
  return Math.max(interval, ceilDiv(ceilDiv(to - from, MAX_POINTS), interval) * interval);
}

// Same as server._range: default pick_step, explicit step clamped.
function resolveStep(from, to, step, interval) {
  if (!(to > from)) throw new Error("from must be < to");
  if (step == null) return pickStep(from, to, interval);
  if (!(step > 0)) throw new Error("step must be > 0");
  step = Math.max(step, interval, ceilDiv(to - from, MAX_EXPLICIT_POINTS));
  return ceilDiv(step, interval) * interval;
}

// Re-bucket a columnar dataset (absolute ts) exactly like Store.query_system/query_apps.
export function bucketize(dataset, from, to, step, top = 15) {
  const { system, apps } = dataset;
  const first = Math.floor(from / step) * step;
  const nb = Math.max(0, ceilDiv(to - first, step));
  const idx = [];
  for (let i = 0; i < system.ts.length; i++) {
    const t = system.ts[i];
    if (t >= first && t < to) idx.push(i);
  }
  const bucketOf = (i) => (Math.floor(system.ts[i] / step) * step - first) / step;

  // system: AVG over non-null (CAST AS INT), MAX(pressure), MAX(used), COUNT(*)
  const count = new Array(nb).fill(0);
  const sum = SYS_COLS.map(() => new Array(nb).fill(0));
  const nn = SYS_COLS.map(() => new Array(nb).fill(0));
  const pmax = new Array(nb).fill(null), umax = new Array(nb).fill(null);
  for (const i of idx) {
    const b = bucketOf(i);
    count[b]++;
    SYS_COLS.forEach((c, k) => {
      const v = system[c][i];
      if (v != null) { sum[k][b] += v; nn[k][b]++; }
    });
    const p = system.pressure[i], u = system.used[i];
    if (p != null && (pmax[b] == null || p > pmax[b])) pmax[b] = p;
    if (u != null && (umax[b] == null || u > umax[b])) umax[b] = u;
  }
  const points = [];
  for (let b = 0; b < nb; b++) {
    const pt = { ts: first + b * step };
    SYS_COLS.forEach((c, k) => { pt[c] = nn[k][b] ? Math.trunc(sum[k][b] / nn[k][b]) : null; });
    pt.pressure = pmax[b];
    pt.used_max = umax[b];
    points.push(pt);
  }

  // apps: rss = SUM/system count (missing = 0), rss_max = MAX, procs = SUM//count
  const per = {}, total = {};
  for (const [name, a] of Object.entries(apps)) {
    for (const i of idx) {
      const r = a.rss[i];
      if (r == null) continue;
      const b = bucketOf(i);
      const s = (per[name] ??= new Map());
      const cur = s.get(b) ?? [0, 0, 0];
      s.set(b, [cur[0] + r, Math.max(cur[1], r), cur[2] + (a.procs[i] ?? 0)]);
      total[name] = (total[name] ?? 0) + r;
    }
  }
  let names = Object.keys(per).filter((n) => n !== OTHER_SMALL).sort((x, y) => total[y] - total[x]);
  if (top !== "all" && top != null) names = names.slice(0, top);
  if (per[OTHER_SMALL]) names.push(OTHER_SMALL);
  const out = {};
  for (const n of names) {
    out[n] = points.map((pt, b) => {
      const c = count[b];
      if (!c) return { ts: pt.ts, rss: null, rss_max: null, procs: null };
      const [s, mx, pr] = per[n].get(b) ?? [0, 0, 0];
      return { ts: pt.ts, rss: Math.floor(s / c), rss_max: mx, procs: Math.floor(pr / c) };
    });
  }
  return { system: { step, points }, apps: { step, apps: out } };
}

// Static demo data. Relative ts (seconds from day-1 local midnight) are anchored to the
// viewer's local timezone so the demo workday lands 9-5 and the data ends at/before `now`.
// `to` is exclusive, same as LiveSource.
export class DemoSource {
  constructor(dataset, now = Date.now() / 1000) {
    const m = dataset.meta;
    let off = 0;
    if (m.relative) {
      const span = m.last_ts - m.first_ts + m.interval;
      off = new Date((now - span) * 1000).setHours(0, 0, 0, 0) / 1000;
      // ponytail: whole-day shifts keep 9-5 alignment; DST ignored
      while (off + m.last_ts > now) off -= 86400;
    }
    this.data = { ...dataset, system: { ...dataset.system, ts: dataset.system.ts.map((t) => t + off) } };
    this.info = { mode: "demo", host: m.host, os: m.os, total_ram: m.total_ram, interval: m.interval,
                  first_ts: m.first_ts + off, last_ts: m.last_ts + off, version: m.version };
  }

  async meta() { return { ...this.info }; }

  async system(from, to, step) {
    step = resolveStep(from, to, step, this.info.interval);
    return bucketize(this.data, from, to, step).system;
  }

  async apps(from, to, { step, top = 15 } = {}) {
    step = resolveStep(from, to, step, this.info.interval);
    return bucketize(this.data, from, to, step, top).apps;
  }
}

// Local server API. Callers pass `to = now + 1` for live ranges (store `to` is exclusive).
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
  system(from, to, step) { return this._get("system", { from, to, step }); }
  apps(from, to, { step, top } = {}) { return this._get("apps", { from, to, step, top }); }
}

// Live server if /api/meta answers within 1.5 s, else the static demo dataset.
export async function loadSource() {
  try {
    const r = await fetch("/api/meta", { signal: AbortSignal.timeout(1500) });
    if (r.ok && (await r.json()).mode === "live") return new LiveSource();
  } catch { /* no local server: fall through to demo */ }
  const r = await fetch("./sample.json");
  if (!r.ok) throw new Error(`sample.json: HTTP ${r.status}`);
  return new DemoSource(await r.json());
}
