// Pure analytics over API-shaped data. Sizes in bytes, ts in unix seconds.
import { OTHER_SMALL } from "./sources.js";

export const MB = 2 ** 20;
export const LEAK_MIN_SLOPE = 50 * MB; // per hour
export const LEAK_MIN_R2 = 0.8;
export const LEAK_MIN_GROWTH = 200 * MB;
export const LEAK_MIN_SPAN = 7200; // shortest sliding window, s
export const LEAK_MIN_PRESENCE = 0.8;
export const LEAK_MIN_POINTS = 8; // guards edge windows with 2-3 points
export const SPIKE_RISE_FRAC = 0.10;
export const SPIKE_WINDOW = 300;
export const EXPLAIN_WINDOW = 300;
const LEAK_WINDOWS = [LEAK_MIN_SPAN, 6 * 3600, 24 * 3600];
const PRESSURE_NORMAL = 1; // macOS kern.memorystatus_vm_pressure_level

// Stacked layers that always sum to totalRam on every platform. RSS double-counts shared
// memory, so Apps is clamped at `used`. Available is shown as total - used (Linux reports
// an `available` that does not add up), never the raw field.
export function stackLayers(points, apps, totalRam) {
  const series = Object.values(apps);
  const out = { ts: [], apps: [], otherSystem: [], available: [] };
  points.forEach((p, i) => {
    out.ts.push(p.ts);
    if (p.used == null) {
      out.apps.push(null); out.otherSystem.push(null); out.available.push(null);
      return;
    }
    const a = Math.min(series.reduce((s, x) => s + (x[i]?.rss ?? 0), 0), p.used);
    out.apps.push(a);
    out.otherSystem.push(Math.max(0, p.used - a));
    out.available.push(Math.max(0, totalRam - p.used));
  });
  return out;
}

const mean = (v) => (v.length ? v.reduce((a, b) => a + b, 0) / v.length : null);

export function topApps(apps) {
  return Object.entries(apps).map(([name, s]) => {
    const live = s.filter((p) => p.rss != null);
    return {
      name,
      avg: Math.floor(mean(live.map((p) => p.rss)) ?? 0),
      peak: Math.max(0, ...live.map((p) => p.rss_max)),
      procs: Math.round(mean(live.map((p) => p.procs)) ?? 0),
      spark: s.map((p) => p.rss),
    };
  }).sort((a, b) => b.avg - a.avg);
}

// Least squares, x in hours, y in bytes.
function fit(pts) {
  const n = pts.length, t0 = pts[0].ts;
  const xs = pts.map((p) => (p.ts - t0) / 3600), ys = pts.map((p) => p.rss);
  const mx = mean(xs), my = mean(ys);
  let sxy = 0, sxx = 0, stot = 0;
  for (let i = 0; i < n; i++) {
    sxy += (xs[i] - mx) * (ys[i] - my);
    sxx += (xs[i] - mx) ** 2;
    stot += (ys[i] - my) ** 2;
  }
  const slope = sxx ? sxy / sxx : 0;
  let sres = 0;
  for (let i = 0; i < n; i++) sres += (ys[i] - (my + slope * (xs[i] - mx))) ** 2;
  return { slope, r2: stot ? 1 - sres / stot : 0, hours: xs[n - 1] };
}

// Sliding-window leak detection (a leak ending in a restart vanishes in a whole-range fit).
// "Present" = rss > 0 in a non-gap bucket; fit uses present points only.
export function detectLeaks(apps, step) {
  const out = [];
  for (const [name, s] of Object.entries(apps)) {
    // the small-apps bucket grows as apps churn into it; an aggregate, not a leak
    if (name === OTHER_SMALL) continue;
    const best = leakOf(s, step);
    if (best) out.push({ name, ...best });
  }
  return out;
}

// Same detector over /api/processes series: [{id, pid, name, app, cmdline, slopePerHour, ...}].
export function processLeaks(processes, step) {
  const out = [];
  for (const [id, p] of Object.entries(processes)) {
    const best = leakOf(p.series, step);
    if (best) out.push({ id, pid: p.pid, name: p.name, app: p.app, cmdline: p.cmdline, ...best });
  }
  return out;
}

// Strongest leak window of one series, or null.
function leakOf(s, step) {
  if (!s.length) return null;
  const range = s.length * step;
  let best = null;
  for (const L of LEAK_WINDOWS) {
    if (L > range) continue;
    const k = Math.ceil(L / step);
    const stride = Math.max(1, Math.floor(L / 4 / step));
    const starts = [];
    for (let i = 0; i + k <= s.length; i += stride) starts.push(i);
    // final window ending at the last bucket, so a leak happening right now is seen
    if (starts.at(-1) !== s.length - k) starts.push(s.length - k);
    for (const i of starts) {
      const win = s.slice(i, i + k);
      const nonGap = win.filter((p) => p.rss != null);
      const pts = nonGap.filter((p) => p.rss > 0);
      if (pts.length < LEAK_MIN_POINTS || pts.length < LEAK_MIN_PRESENCE * nonGap.length) continue;
      const { slope, r2, hours } = fit(pts);
      const growth = slope * hours;
      if (slope > LEAK_MIN_SLOPE && r2 > LEAK_MIN_R2 && growth > LEAK_MIN_GROWTH &&
          (!best || growth > best.growth)) {
        best = { slopePerHour: slope, r2, growth, startTs: pts[0].ts, endTs: pts.at(-1).ts };
      }
    }
  }
  return best;
}

// A spike starts when used_max rises > SPIKE_RISE_FRAC*totalRam within the window, or
// pressure leaves normal. Windows containing a gap are never compared across.
export function detectSpikes(points, totalRam, step) {
  const k = Math.ceil(Math.max(SPIKE_WINDOW, 2 * step) / step);
  const hits = [];
  points.forEach((p, i) => {
    if (p.used_max == null) return;
    if (p.pressure != null && p.pressure > PRESSURE_NORMAL) {
      hits.push({ s: i, e: i, reason: "pressure" });
    }
    if (i < k) return;
    const win = points.slice(i - k, i + 1);
    if (win.some((q) => q.used_max == null)) return;
    let lo = i - k;
    for (let j = i - k; j < i; j++) if (points[j].used_max < points[lo].used_max) lo = j;
    if (p.used_max - points[lo].used_max > SPIKE_RISE_FRAC * totalRam) {
      hits.push({ s: lo, e: i, reason: "rise" });
    }
  });
  hits.sort((a, b) => a.s - b.s);
  const merged = [];
  for (const h of hits) {
    const m = merged.at(-1);
    if (m && h.s <= m.e + 1) {
      m.e = Math.max(m.e, h.e);
      if (h.reason === "rise") m.reason = "rise";
    } else merged.push({ ...h });
  }
  // base = lowest used_max before the max (a pressure hit can open the span high).
  // peakTs = first bucket after base reaching 90% of the rise (top of the ramp). The argmax
  // itself drifts across a long plateau with noise, away from the growth explainSpike sees.
  // peakEnd = peakTs + step: exclusive end of the peak bucket; use it with spikeWindow().
  return merged.map(({ s, e, reason }) => {
    let mx = s;
    for (let j = s; j <= e; j++) if (points[j].used_max > points[mx].used_max) mx = j;
    let lo = s;
    for (let j = s; j <= mx; j++) if (points[j].used_max < points[lo].used_max) lo = j;
    const base = points[lo].used_max, top = points[mx].used_max;
    let pk = lo;
    while (points[pk].used_max - base < 0.9 * (top - base)) pk++;
    return { startTs: points[s].ts, peakTs: points[pk].ts, peakEnd: points[pk].ts + step,
             rise: top - base, reason };
  });
}

// Range for the full-resolution apps fetch behind explainSpike; covers the whole rise.
export const spikeWindow = (spike) => ({ from: spike.startTs - EXPLAIN_WINDOW, to: spike.peakEnd });

// Which apps grew over the whole detected rise, [startTs - EXPLAIN_WINDOW, peakEnd),
// ranked by growth, not size. At coarse steps a fixed window before the peak misses the ramp.
export function explainSpike(apps, spike) {
  const out = [];
  for (const [name, s] of Object.entries(apps)) {
    if (name === OTHER_SMALL) continue;
    const growth = spikeGrowth(s, spike);
    if (growth > 0) out.push({ name, growth });
  }
  return out.sort((x, y) => y.growth - x.growth);
}

// Same growth rule over /api/processes series (fetched over spikeWindow(spike)).
export function spikeProcesses(processes, spike) {
  const out = [];
  for (const [id, p] of Object.entries(processes)) {
    const growth = spikeGrowth(p.series, spike);
    if (growth > 0) out.push({ id, pid: p.pid, name: p.name, app: p.app, cmdline: p.cmdline, growth });
  }
  return out.sort((x, y) => y.growth - x.growth);
}

// rss(last point before peakEnd) - rss(first point at/after startTs - EXPLAIN_WINDOW); null if none.
function spikeGrowth(s, spike) {
  const from = spike.startTs - EXPLAIN_WINDOW, end = spike.peakEnd;
  const live = s.filter((p) => p.rss != null);
  const a = live.find((p) => p.ts >= from);
  const b = live.findLast((p) => p.ts < end);
  return a && b ? b.rss - a.rss : null;
}

// Local day-of-week (0=Sun) x hour average `used`; null under 2 days of data.
// Coverage = last - first + step (bucket width inferred from the points).
export function heatmap(points) {
  const live = points.filter((p) => p.used != null);
  const step = points.length > 1 ? points[1].ts - points[0].ts : 0;
  if (!live.length || live.at(-1).ts - live[0].ts + step < 2 * 86400) return null;
  const sum = Array.from({ length: 7 }, () => new Array(24).fill(0));
  const n = Array.from({ length: 7 }, () => new Array(24).fill(0));
  for (const p of live) {
    const d = new Date(p.ts * 1000);
    sum[d.getDay()][d.getHours()] += p.used;
    n[d.getDay()][d.getHours()]++;
  }
  return sum.map((r, d) => r.map((v, h) => (n[d][h] ? v / n[d][h] : null)));
}
