// Shared formatting and inline-SVG helpers. No imports, so every module can use them.
const MiB = 2 ** 20, GiB = 2 ** 30;

export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
export const fmtBytes = (b) => (b == null ? "–" : Math.abs(b) >= GiB ? `${(b / GiB).toFixed(1)} GiB` : `${(b / MiB).toFixed(1)} MiB`);
export const fmtCpu = (c) => (c == null ? "–" : `${(c / 10).toFixed(1)}%`); // cpu is percent x10
export const fmtTime = (ts) => new Date(ts * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
export const procKey = (p) => `${p.pid}:${p.start_ts}`;

// SVG path over a 0..100 x h box. pts: [x in 0..1, value or null]; null breaks the line.
function pathD(pts, h) {
  const v = pts.map((p) => p[1]).filter((y) => y != null);
  if (!v.length) return "";
  const lo = Math.min(...v), hi = Math.max(...v);
  let d = "", pen = false;
  for (const [x, y] of pts) {
    if (y == null) { pen = false; continue; }
    const yy = hi === lo ? h / 2 : h - 1 - ((y - lo) / (hi - lo)) * (h - 2);
    d += `${pen ? "L" : "M"}${(x * 100).toFixed(2)},${yy.toFixed(2)}`;
    pen = true;
  }
  return d;
}

// Table sparkline over evenly spaced values.
export function spark(values) {
  const n = Math.max(1, values.length - 1), d = pathD(values.map((y, i) => [i / n, y]), 21);
  return d ? `<svg class="spark" viewBox="0 0 100 21" preserveAspectRatio="none" aria-hidden="true"><path d="${d}"/></svg>` : "";
}

// Small line chart of series[key] over time, with min/max labels. fmt formats the labels.
export function lineChart(series, key, fmt, label) {
  const v = series.map((s) => s[key]).filter((y) => y != null);
  if (v.length < 2) return `<p class="empty">Not enough samples yet.</p>`;
  const t0 = series[0].ts, span = series[series.length - 1].ts - t0 || 1;
  const d = pathD(series.map((s) => [(s.ts - t0) / span, s[key]]), 40);
  return `<figure class="mini">
    <svg viewBox="0 0 100 40" preserveAspectRatio="none" role="img" aria-label="${esc(label)}"><path d="${d}"/></svg>
    <figcaption><span>min <span class="num">${fmt(Math.min(...v))}</span></span><span>max <span class="num">${fmt(Math.max(...v))}</span></span></figcaption>
  </figure>`;
}
