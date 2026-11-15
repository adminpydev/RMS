// RMS dashboard. One module, one render function per section. Data comes from
// LiveSource (local server) or DemoSource (sample.json); analytics are pure functions.
import uPlot from "./vendor/uPlot.esm.js";
import { loadSource, LiveSource, OTHER_SMALL } from "./sources.js";
import { stackLayers, topApps, detectLeaks, detectSpikes, spikeWindow, explainSpike, heatmap } from "./analytics.js";

const $ = (s) => document.querySelector(s);
const GiB = 2 ** 30, MiB = 2 ** 20;
const PRESETS = { "1h": 3600, "6h": 6 * 3600, "24h": 86400, "7d": 7 * 86400 };
const TOP_N = 15;
const MAX_SPIKES = 12; // ponytail: caps per-spike apps fetches; paginate if ranges get busier
const RSS_NOTE = "App memory uses RSS, which counts shared memory more than once.";
const LOST = "Lost connection to RMS. Retrying…";
const OS = { darwin: "macOS", linux: "Linux", win32: "Windows" };
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

let src, meta, view = null, timer = null, gen = 0, selectedApp = null;
let range = { preset: "24h" }; // or { from, to } for custom / zoomed ranges
const charts = new Map(); // container element -> uPlot

// ---------- formatting ----------

const fmtBytes = (b) => (b == null ? "–" : Math.abs(b) >= GiB ? `${(b / GiB).toFixed(1)} GiB` : `${(b / MiB).toFixed(1)} MiB`);
const plus = (b) => `+${fmtBytes(b)}`;
const fmtTime = (ts) => new Date(ts * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const pad = (n) => String(n).padStart(2, "0");
function toInput(ts) {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ---------- range + data ----------

const isLive = () => src instanceof LiveSource;
// Demo data ends at/before now (whole-day anchoring), so demo ranges end at the data's end.
const end = () => (isLive() ? Math.floor(Date.now() / 1000) + 1 : meta.last_ts + meta.interval);
function span() {
  if (range.preset) { const e = end(); return [e - PRESETS[range.preset], e]; }
  return [range.from, range.to];
}

function setRange(r) { range = r; refresh(); }

function schedule(always = false) {
  clearTimeout(timer);
  if (isLive() && (always || range.preset)) timer = setTimeout(refresh, (meta?.interval ?? 10) * 1000);
}

function setStatus(msg, error = false) {
  const el = $("#status");
  el.textContent = msg ?? "";
  el.classList.toggle("error", error);
}

async function refresh() {
  clearTimeout(timer);
  const my = ++gen;
  try {
    meta = await src.meta();
    renderHeader();
    if (meta.first_ts == null) {
      if (my !== gen) return;
      setStatus(null);
      $("#main").hidden = true;
      const c = $("#collecting");
      c.hidden = false;
      c.textContent = `Collecting data… first sample in ${meta.interval} s.`;
      return schedule(true);
    }
    const [from, to] = span();
    const sys = await src.system(from, to);
    const [apps, hist] = await Promise.all([
      src.apps(from, to, { step: sys.step, top: "all" }),
      src.system(meta.first_ts, end()), // daily pattern uses all history, not just the range
    ]);
    const spikes = detectSpikes(sys.points, meta.total_ram, sys.step).slice(-MAX_SPIKES);
    const causes = await Promise.all(spikes.map(async (s) => {
      const w = spikeWindow(s);
      const a = await src.apps(w.from, w.to, { step: meta.interval, top: "all" });
      return explainSpike(a.apps, s.peakEnd).slice(0, 5);
    }));
    if (my !== gen) return;
    view = { from, to, step: sys.step, points: sys.points, apps: apps.apps,
             spikes: spikes.map((s, i) => ({ ...s, causes: causes[i] })), heat: heatmap(hist.points) };
    setStatus(null);
    render();
    schedule();
  } catch (e) {
    if (my !== gen) return;
    console.error(e);
    setStatus(isLive() ? LOST : `Could not load data: ${e.message}`, true);
    schedule(true);
  }
}

// ---------- charts ----------

function zoomFromSelect(u) {
  const { left, width } = u.select;
  if (width < 2) return;
  const from = Math.floor(u.posToVal(left, "x")), to = Math.ceil(u.posToVal(left + width, "x"));
  u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
  if (to - from >= 60) setRange({ from, to });
}

function opts(height, series, { yMax, hooks = {} } = {}) {
  const axis = { stroke: css("--muted"), font: `11px ${css("--mono")}`,
                 grid: { stroke: css("--grid"), width: 1 }, ticks: { stroke: css("--grid"), width: 1 } };
  return {
    height,
    series: [{}, ...series],
    axes: [axis, { ...axis, size: 84, values: (u, v) => v.map(fmtBytes) }],
    scales: { y: { range: (u, mn, mx) => [0, Math.max(yMax ?? 0, mx * 1.08, MiB)] } },
    cursor: { sync: { key: "rms" }, drag: { x: true, y: false, setScale: false } },
    hooks: { ...hooks, setSelect: [zoomFromSelect] },
  };
}

// Reuse the chart when its shape is unchanged (keeps hover state across polls).
function chart(el, key, o, data) {
  const c = charts.get(el);
  if (c && c.key === key) { c.u.setData(data); return c.u; }
  c?.u.destroy();
  const u = new uPlot({ width: el.clientWidth || 300, ...o }, data, el);
  charts.set(el, { u, key });
  ro.observe(el);
  return u;
}

function dropCharts(inside) {
  for (const [el, c] of charts) {
    if (inside && !inside.contains(el)) continue;
    c.u.destroy();
    charts.delete(el);
    ro.unobserve(el);
  }
}

const ro = new ResizeObserver((entries) => {
  for (const e of entries) {
    const u = charts.get(e.target)?.u, w = Math.floor(e.contentRect.width);
    if (u && w && w !== u.width) u.setSize({ width: w, height: u.height });
  }
});

const line = (label, color, extra = {}) => ({ label, stroke: css(color), width: 1.5, value: (u, v) => fmtBytes(v), ...extra });

function rssChart(el, s, height) {
  return chart(el, "rss", opts(height, [
    line("RSS avg", "--apps", { fill: css("--apps-fill") }),
    line("RSS peak", "--other", { width: 1, dash: [3, 3] }),
  ]), [s.map((p) => p.ts), s.map((p) => p.rss), s.map((p) => p.rss_max)]);
}

function empty(box, text) {
  box.innerHTML = `<p class="empty">${esc(text)}</p>`;
}

// ---------- sections ----------

function renderHeader() {
  const badge = $("#mode");
  badge.hidden = false;
  badge.textContent = meta.mode === "live" ? "Live" : "Demo";
  badge.classList.toggle("live", meta.mode === "live");
  $("#banner").hidden = meta.mode !== "demo";
  $("#host").textContent = `${meta.host} · ${OS[meta.os] ?? meta.os} · ${fmtBytes(meta.total_ram)} RAM`;
}

function renderRange() {
  document.querySelectorAll("[data-range]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.range === range.preset)));
  const [from, to] = span();
  for (const [id, ts] of [["#from", from], ["#to", to]]) {
    const el = $(id);
    if (document.activeElement !== el) el.value = toInput(ts);
  }
}

function renderSystem(v) {
  const L = stackLayers(v.points, v.apps, meta.total_ram);
  const used = L.apps.map((a, i) => (a == null ? null : a + L.otherSystem[i]));
  const total = used.map((u, i) => (u == null ? null : u + L.available[i]));
  const layer = (arr) => (u, raw, si, i) => fmtBytes(i == null ? null : arr[i]);
  // Layers are drawn as cumulative areas, back to front; the legend shows each layer's own size.
  const series = [
    line("Available", "--avail", { width: 1, fill: css("--avail-fill"), value: layer(L.available) }),
    line("Other (system)", "--other", { width: 1, fill: css("--other-fill"), value: layer(L.otherSystem) }),
    line("Apps", "--apps", { width: 1, fill: css("--apps-fill"), value: layer(L.apps) }),
  ];
  const data = [L.ts, total, used, L.apps];
  for (const [k, label, color] of [["cached", "Cached", "--cached"], ["compressed", "Compressed", "--compressed"], ["swap_used", "Swap used", "--swap"]]) {
    const col = v.points.map((p) => p[k]);
    if (col.every((x) => x == null)) continue;
    series.push(line(label, color, { dash: [5, 3] }));
    data.push(col);
  }
  const el = $("#sys-chart");
  const u = chart(el, `sys:${series.map((s) => s.label)}`, opts(260, series, {
    yMax: meta.total_ram, hooks: { setSize: [alignPressure] },
  }), data);
  u.root.querySelectorAll(".u-legend .u-series")[3]?.setAttribute("title", RSS_NOTE);
  renderPressure(v.points);
  alignPressure(u);
}

function renderPressure(points) {
  const wrap = $("#pressure-wrap");
  wrap.hidden = !points.some((p) => p.pressure != null);
  if (wrap.hidden) return;
  // macOS kern.memorystatus_vm_pressure_level: 1 normal, 2 warning, 4 critical
  const color = (p) => (p == null ? "transparent" : p >= 4 ? "var(--p-crit)" : p >= 2 ? "var(--p-warn)" : "var(--p-ok)");
  const n = points.length, stops = [];
  let start = 0;
  for (let i = 1; i <= n; i++) {
    if (i < n && color(points[i].pressure) === color(points[start].pressure)) continue;
    const c = color(points[start].pressure);
    stops.push(`${c} ${(start / n) * 100}% ${(i / n) * 100}%`);
    start = i;
  }
  $("#pressure").style.background = `linear-gradient(to right, ${stops.join(", ")})`;
}

// Keep the pressure strip under the plot area, not the y-axis.
function alignPressure(u) {
  const el = $("#pressure"), dpr = devicePixelRatio || 1;
  el.style.marginLeft = `${u.bbox.left / dpr}px`;
  el.style.width = `${u.bbox.width / dpr}px`;
}

function spark(values) {
  const live = values.filter((x) => x != null);
  if (!live.length) return "";
  const lo = Math.min(...live), hi = Math.max(...live), n = Math.max(1, values.length - 1);
  let d = "", pen = false;
  values.forEach((x, i) => {
    if (x == null) { pen = false; return; }
    const y = hi === lo ? 10 : 20 - ((x - lo) / (hi - lo)) * 19;
    d += `${pen ? "L" : "M"}${((i / n) * 100).toFixed(2)},${y.toFixed(2)}`;
    pen = true;
  });
  return `<svg class="spark" viewBox="0 0 100 21" preserveAspectRatio="none" aria-hidden="true"><path d="${d}"/></svg>`;
}

function renderApps(v) {
  const rows = topApps(v.apps).slice(0, TOP_N);
  if (selectedApp && !v.apps[selectedApp]) selectedApp = null;
  $("#apps-body").innerHTML = rows.length
    ? rows.map((r) => `<tr tabindex="0" data-app="${esc(r.name)}" aria-current="${r.name === selectedApp}">
        <th scope="row" title="${esc(r.name)}">${esc(r.name)}</th>
        <td class="num">${fmtBytes(r.avg)}</td><td class="num">${fmtBytes(r.peak)}</td>
        <td class="num procs">${r.procs}</td><td>${spark(r.spark)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="empty">No app data in this range.</td></tr>`;
  renderAppDetail(v);
}

function renderAppDetail(v) {
  const box = $("#app-detail"), el = $("#app-chart");
  box.hidden = !selectedApp;
  if (!selectedApp) return dropCharts(box);
  $("#app-title").textContent = selectedApp;
  el.setAttribute("aria-label", `Memory used by ${selectedApp} over the selected range`);
  rssChart(el, v.apps[selectedApp], 200);
}

function selectRow(tr) {
  if (!tr?.dataset.app) return;
  selectedApp = selectedApp === tr.dataset.app ? null : tr.dataset.app;
  renderApps(view);
  document.querySelector(`#apps-body tr[data-app="${CSS.escape(tr.dataset.app)}"]`)?.focus();
}

function renderLeaks(v) {
  const box = $("#leaks");
  dropCharts(box);
  // The small-apps bucket grows as apps churn into it; that is not a leak.
  const leaks = detectLeaks(v.apps, v.step).filter((l) => l.name !== OTHER_SMALL).sort((a, b) => b.growth - a.growth);
  if (!leaks.length) return empty(box, "No leaks detected in this range.");
  box.innerHTML = leaks.map((l, i) => `<article class="card">
      <h3>${esc(l.name)}</h3>
      <div class="stat">${plus(l.slopePerHour)}/h</div>
      <p class="meta">${plus(l.growth)} from ${fmtTime(l.startTs)} to ${fmtTime(l.endTs)} · fit R² <span class="mono">${l.r2.toFixed(2)}</span></p>
      <div class="chart" data-leak="${i}" role="figure" aria-label="Memory used by ${esc(l.name)} over the selected range"></div>
    </article>`).join("");
  leaks.forEach((l, i) => rssChart(box.querySelector(`[data-leak="${i}"]`), v.apps[l.name], 140));
}

function renderSpikes(v) {
  const box = $("#spikes");
  if (!v.spikes.length) return empty(box, "No spikes in this range.");
  box.innerHTML = [...v.spikes].reverse().map((s) => `<article class="card">
      <h3>${fmtTime(s.peakTs)}</h3>
      <div class="stat">${plus(s.rise)}</div>
      <p class="meta">${s.reason === "pressure" ? "Memory pressure left normal" : "Used memory rose fast"}</p>
      ${s.causes.length
        ? `<ol aria-label="Top causes">${s.causes.map((c) => `<li><span>${esc(c.name)}</span><span class="num">${plus(c.growth)}</span></li>`).join("")}</ol>`
        : `<p class="empty">No single app grew. Likely system memory.</p>`}
    </article>`).join("");
}

function renderPattern(v) {
  const box = $("#heat");
  if (!v.heat) return empty(box, "Collect for 2+ days to see patterns.");
  const vals = v.heat.flat().filter((x) => x != null);
  const lo = Math.min(...vals), hi = Math.max(...vals);
  let html = `<span></span>${Array.from({ length: 24 }, (_, h) => `<span class="h">${h % 6 ? "" : pad(h)}</span>`).join("")}`;
  for (const d of [1, 2, 3, 4, 5, 6, 0]) {
    html += `<span>${DAYS[d]}</span>`;
    html += v.heat[d].map((x, h) => (x == null
      ? `<span class="c na" title="${DAYS[d]} ${pad(h)}:00 · no data"></span>`
      : `<span class="c" style="--v:${hi > lo ? ((x - lo) / (hi - lo)).toFixed(3) : 0.5}" title="${DAYS[d]} ${pad(h)}:00 · ${fmtBytes(x)} used on average"></span>`)).join("");
  }
  box.innerHTML = `<div class="heat" role="img" aria-label="Average used memory by weekday and hour, ${fmtBytes(lo)} to ${fmtBytes(hi)}">${html}</div>
    <div class="heat-key"><span class="num">${fmtBytes(lo)}</span><span class="bar"></span><span class="num">${fmtBytes(hi)}</span><span>average used</span></div>`;
}

function render() {
  $("#collecting").hidden = true;
  $("#main").hidden = false;
  renderRange();
  renderSystem(view);
  renderApps(view);
  renderLeaks(view);
  renderSpikes(view);
  renderPattern(view);
}

// ---------- wiring ----------

function bind() {
  document.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => setRange({ preset: b.dataset.range })));
  const onCustom = () => {
    const from = new Date($("#from").value).getTime() / 1000, to = new Date($("#to").value).getTime() / 1000;
    if (from < to) setRange({ from: Math.floor(from), to: Math.floor(to) });
  };
  $("#from").addEventListener("change", onCustom);
  $("#to").addEventListener("change", onCustom);
  const body = $("#apps-body");
  body.addEventListener("click", (e) => selectRow(e.target.closest("tr")));
  body.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    selectRow(e.target.closest("tr"));
  });
  // Chart colors are read from CSS at creation, so rebuild charts on theme change.
  matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
    dropCharts();
    if (view) render();
  });
}

async function init() {
  bind();
  try {
    src = await loadSource();
  } catch (e) {
    console.error(e);
    return setStatus(`Could not load data: ${e.message}`, true);
  }
  refresh();
}

init();
