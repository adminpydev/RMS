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

// Re-bucket the dataset's `processes` section exactly like Store.query_processes:
// rss = SUM/system-sample-count (missing = 0), rss_max = MAX, cpu = int AVG over present
// non-null cpu, gap buckets all null; ranked by total rss in range (ties by id).
export function bucketProcesses(dataset, from, to, step, { app, top = 25, ensure = false } = {}) {
  const ts = dataset.system.ts;
  const first = Math.floor(from / step) * step;
  const nb = Math.max(0, ceilDiv(to - first, step));
  const count = new Array(nb).fill(0), idx = [];
  for (let i = 0; i < ts.length; i++) {
    if (ts[i] >= first && ts[i] < to) { idx.push(i); count[(Math.floor(ts[i] / step) * step - first) / step]++; }
  }
  const rows = [];
  for (const [id, p] of Object.entries(dataset.processes ?? {})) {
    if (app != null && p.app !== app) continue;
    const per = new Map();
    let total = 0;
    for (const i of idx) {
      const r = p.rss[i];
      if (r == null) continue;
      const b = (Math.floor(ts[i] / step) * step - first) / step;
      const c = per.get(b) ?? [0, 0, 0, 0];
      const cpu = p.cpu?.[i];
      per.set(b, [c[0] + r, Math.max(c[1], r), c[2] + (cpu ?? 0), c[3] + (cpu != null)]);
      total += r;
    }
    if (per.size || ensure) rows.push([id, p, per, total]); // ensure: like get_process
  }
  rows.sort((a, b) => b[3] - a[3] || a[0].localeCompare(b[0], undefined, { numeric: true }));
  const out = {};
  for (const [id, p, per] of top === "all" || top == null ? rows : rows.slice(0, top)) {
    const { rss, cpu, ...meta } = p;
    out[id] = { ...meta, series: count.map((c, b) => {
      const t = first + b * step;
      if (!c) return { ts: t, rss: null, rss_max: null, cpu: null };
      const [s, mx, cs, cn] = per.get(b) ?? [0, 0, 0, 0];
      return { ts: t, rss: Math.floor(s / c), rss_max: mx, cpu: cn ? Math.trunc(cs / cn) : null };
    }) };
  }
  return out;
}

// Static demo data. Relative ts (seconds from day-1 local midnight) are anchored to the
// viewer's local timezone so the demo workday lands 9-5 and the data ends at/before `now`.
// `to` is exclusive, same as LiveSource.
export class DemoSource {
  constructor(dataset, now = Date.now() / 1000, clock = () => Date.now() / 1000) {
    const m = dataset.meta;
    let off = 0;
    if (m.relative) {
      const span = m.last_ts - m.first_ts + m.interval;
      off = new Date((now - span) * 1000).setHours(0, 0, 0, 0) / 1000;
      // ponytail: whole-day shifts keep 9-5 alignment; DST ignored
      while (off + m.last_ts > now) off -= 86400;
    }
    const ts = dataset.system.ts.map((t) => t + off);
    const processes = {};
    for (const [id, p] of Object.entries(dataset.processes ?? {})) {
      const i0 = p.rss.findIndex((r) => r != null), i1 = p.rss.findLastIndex((r) => r != null);
      processes[id] = { ...p, start_ts: p.start_ts + off,
                        first_seen: ts[i0] ?? null, last_seen: ts[i1] ?? null };
    }
    this.data = { ...dataset, system: { ...dataset.system, ts }, processes };
    this.info = { mode: "demo", host: m.host, os: m.os, total_ram: m.total_ram, interval: m.interval,
                  first_ts: m.first_ts + off, last_ts: m.last_ts + off, version: m.version,
                  features: ["processes", "live"] };
    this.clock = clock;
    this.t0 = clock();
  }

  async meta() { return { ...this.info, features: [...this.info.features] }; }

  async system(from, to, step) {
    step = resolveStep(from, to, step, this.info.interval);
    return bucketize(this.data, from, to, step).system;
  }

  async apps(from, to, { step, top = 15 } = {}) {
    step = resolveStep(from, to, step, this.info.interval);
    return bucketize(this.data, from, to, step, top).apps;
  }

  // Same shape as /api/processes. first_seen/last_seen are internal (not in the API list).
  async processes(from, to, { step, app, top = 25 } = {}) {
    step = resolveStep(from, to, step, this.info.interval);
    const ps = bucketProcesses(this.data, from, to, step, { app, top });
    for (const p of Object.values(ps)) { delete p.first_seen; delete p.last_seen; }
    return { step, processes: ps };
  }

  // Same shape and parent/children rules as Store.get_process; unknown id throws like a 404.
  async process(id, from, to, step) {
    step = resolveStep(from, to, step, this.info.interval);
    const all = this.data.processes, d = Object.hasOwn(all, id) ? all[id] : null;
    if (!d) throw new Error("not found");
    const { rss, cpu, ...meta } = d;
    const ov = ([, p]) => p.first_seen < to && p.last_seen >= from;
    const ref = ([k, p]) => ({ id: k, pid: p.pid, name: p.name });
    let parent = null;
    if (d.ppid != null) {
      const c = Object.entries(all).filter((e) => e[1].pid === d.ppid && e[0] !== id &&
                                           e[1].start_ts <= d.start_ts && ov(e));
      c.sort((a, b) => b[1].start_ts - a[1].start_ts);
      parent = c.length ? ref(c[0]) : { id: null, pid: d.ppid, name: null };
    }
    const children = Object.entries(all)
      .filter((e) => e[1].ppid === d.pid && e[0] !== id && e[1].start_ts >= d.start_ts && ov(e))
      .sort((a, b) => a[0].localeCompare(b[0], undefined, { numeric: true })).map(ref);
    const series = bucketProcesses({ system: this.data.system, processes: { [id]: d } }, from, to, step,
                                   { top: "all", ensure: true })[id].series;
    return { id, ...meta, parent, children, series };
  }

  // Simulated /api/live: a virtual clock replays the last day from 09:00 at 60x real time,
  // wrapping at the dataset end. Spark = 30 s samples over the last 10 min.
  async live() {
    const { first_ts, last_ts, interval, total_ram } = this.info;
    const start = first_ts + Math.floor((last_ts - first_ts) / 86400) * 86400 + 9 * 3600;
    const P = last_ts - start + interval, x = 60 * (this.clock() - this.t0);
    const t = start + Math.floor(((x % P) + P) % P); // positive modulo: clock skew stays in range
    const ts = this.data.system.ts, sys = this.data.system;
    // ponytail: samples are regular in the shipped dataset; a binary search if gaps appear
    const at = (x) => Math.floor((x - first_ts) / interval);
    const i = Math.min(at(t), ts.length - 1), j5 = at(ts[i] - 300), j10 = at(ts[i] - 600);
    const processes = [];
    for (const [id, p] of Object.entries(this.data.processes)) {
      const rss = p.rss[i];
      if (rss == null) continue;
      const old = j5 >= 0 ? p.rss[j5] : null;
      const spark = [];
      for (let k = Math.max(0, i - 19); k <= i; k++) if (p.rss[k] != null) spark.push(p.rss[k]);
      processes.push({ id, pid: p.pid, ppid: p.ppid, start_ts: p.start_ts, name: p.name, app: p.app,
                       user: p.user, cmdline: p.cmdline, rss, cpu: p.cpu?.[i] ?? null, threads: null,
                       growth_5m: old == null ? null : rss - old, spark });
    }
    return { ts: t, resolution: 2, simulated: true,
             system: { total: total_ram, used: sys.used[i], available: sys.available[i],
                       swap_used: sys.swap_used[i], swap_used_10m_ago: j10 >= 0 ? sys.swap_used[j10] : null,
                       pressure: sys.pressure[i] },
             processes };
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
  processes(from, to, { step, app, top } = {}) { return this._get("processes", { from, to, step, app, top }); }
  process(id, from, to, step) { return this._get(`process/${encodeURIComponent(id)}`, { from, to, step }); }
  live({ fast } = {}) { return this._get("live", { fast: fast ? 1 : null }); }
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
