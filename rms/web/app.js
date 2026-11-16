// RMS dashboard orchestrator: Overview sections, tabs and the /api/live poll. Data comes from
// LiveSource (local server) or DemoSource (sample.json); analytics are pure functions.
// live.js, process.js and insights-ui.js import the shared helpers exported here.
import uPlot from "./vendor/uPlot.esm.js";
import { loadSource, LiveSource, OTHER_SMALL } from "./sources.js";
import { stackLayers, topApps, detectLeaks, leakCulprits, detectSpikes, spikeWindow, explainSpike,
         spikeProcesses, heatmap } from "./analytics.js";
import { insights } from "./insights.js";
import { renderTree, procLink, shortCmd, bindProcess } from "./process.js";
import { setLive, liveError, renderLive, tickLive, isPaused, bindLive } from "./live.js";
import { renderInsights, bindInsights } from "./insights-ui.js";

const $ = (s) => document.querySelector(s);
const GiB = 2 ** 30, MiB = 2 ** 20;
const PRESETS = { "1h": 3600, "6h": 6 * 3600, "24h": 86400, "3d": 3 * 86400, "7d": 7 * 86400 };
const TOP_N = 15;
const MAX_SPIKES = 12; // ponytail: caps per-spike apps fetches; paginate if ranges get busier
const RSS_NOTE = "App memory uses RSS, which counts shared memory more than once.";
const LOST = "Lost connection to RMS. Retrying…";
const BROKEN = "Something went wrong rendering this view.";
const OS = { darwin: "macOS", linux: "Linux", win32: "Windows" };
const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

export let src;
let meta, view = null, timer = null, gen = 0, selectedApp = null;
let tab = "overview", liveTimer = null, liveGen = 0, ticker = null;
const has = (f) => meta?.features?.includes(f) ?? false;
let hist = null, histAt = 0; // daily-pattern history, refetched at most hourly
const HIST_TTL = 3600;
let range = { preset: "24h" }; // or { from, to } for custom / zoomed ranges
const charts = new Map(); // container element -> uPlot

// ---------- formatting ----------

export const fmtBytes = (b) => (b == null ? "–" : Math.abs(b) >= GiB ? `${(b / GiB).toFixed(1)} GiB` : `${(b / MiB).toFixed(1)} MiB`);
const plus = (b) => `+${fmtBytes(b)}`;
export const fmtTime = (ts) => new Date(ts * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
export const css = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const pad = (n) => String(n).padStart(2, "0");
function toInput(ts) {
  const d = new Date(ts * 1000);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

// ---------- range + data ----------

const isLive = () => src instanceof LiveSource;
// Demo data ends at/before now (whole-day anchoring), so demo ranges end at the data's end.
const end = () => (isLive() ? Math.floor(Date.now() / 1000) + 1 : meta.last_ts + meta.interval);
export function span() {
  if (range.preset) {
    const e = end(), f = e - PRESETS[range.preset];
    return [meta.first_ts ? Math.max(f, meta.first_ts - meta.interval) : f, e]; // short history -> fine step
  }
  return [range.from, range.to];
}

function setRange(r) { range = r; refresh(); }

function schedule(always = false) {
  clearTimeout(timer);
  if (tab === "live") return; // Overview resumes with a refresh when its tab is shown again
  if (isLive() && (always || range.preset)) timer = setTimeout(refresh, (meta?.interval ?? 10) * 1000);
}

function setStatus(msg, error = false) {
  const el = $("#status");
  el.textContent = msg ?? "";
  el.classList.toggle("error", error);
}

// Tag data-source failures so only they get the connection message.
const net = (p) => p.catch((e) => { e.net = true; throw e; });

async function refresh() {
  clearTimeout(timer);
  const my = ++gen;
  try {
    meta = await net(src.meta());
    renderHeader();
    enableLive();
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
    const now = Date.now() / 1000;
    const stale = !hist || now - histAt > HIST_TTL;
    // ponytail: top=all apps query costs ~0.5 s CPU per poll on a 7d range; skip polling when step >= 600 s, or add rollups
    const sys = await net(src.system(from, to));
    const procs = has("processes"), treeApp = procs && selectedApp !== OTHER_SMALL ? selectedApp : null;
    const [apps, h, tree] = await Promise.all([
      net(src.apps(from, to, { step: sys.step, top: "all" })),
      // daily pattern uses all history, not just the range
      stale ? net(src.system(meta.first_ts, end())) : hist,
      treeApp ? net(src.processes(from, to, { step: sys.step, app: treeApp, top: "all" })) : null,
    ]);
    if (stale) { hist = h; histAt = now; }
    const spikes = detectSpikes(sys.points, meta.total_ram, sys.step).slice(-MAX_SPIKES);
    const leaks = detectLeaks(apps.apps, sys.step).sort((a, b) => b.growth - a.growth);
    const [causes, culprits] = await Promise.all([
      Promise.all(spikes.map(async (s) => {
        const w = spikeWindow(s);
        if (procs) return spikeProcesses((await net(src.processes(w.from, w.to, { step: meta.interval, top: "all" }))).processes, s).slice(0, 5);
        return explainSpike((await net(src.apps(w.from, w.to, { step: meta.interval, top: "all" }))).apps, s).slice(0, 5);
      })),
      Promise.all(leaks.map((l) => (procs && l.name !== OTHER_SMALL ? fetchCulprits(l, sys.step) : []))),
    ]);
    if (my !== gen) return;
    view = { from, to, step: sys.step, points: sys.points, apps: apps.apps,
             tree: treeApp ? { app: treeApp, ps: tree.processes } : null,
             leaks: leaks.map((l, i) => ({ ...l, culprits: culprits[i] })),
             spikes: spikes.map((s, i) => ({ ...s, causes: causes[i] })), heat: heatmap(hist.points) };
    setStatus(null);
    render();
    schedule();
  } catch (e) {
    if (my !== gen) return;
    console.error(e);
    setStatus(!e.net ? BROKEN : isLive() ? LOST : `Could not load data: ${e.message}`, true);
    schedule(true);
  }
}

// Responsible processes of an app leak, fetched over the leak window (see leakCulprits).
async function fetchCulprits(l, step) {
  const r = await net(src.processes(l.startTs, l.endTs + step, { step, app: l.name, top: "all" }));
  return leakCulprits(r.processes, r.step, l);
}

// ---------- charts ----------

function zoomFromSelect(u) {
  // Synced charts mirror the selection; only the chart the user dragged on zooms.
  if (!u.cursor.event) return;
  const { left, width } = u.select;
  if (width < 2) return;
  const from = Math.floor(u.posToVal(left, "x")), to = Math.ceil(u.posToVal(left + width, "x"));
  u.setSelect({ left: 0, top: 0, width: 0, height: 0 }, false);
  if (to - from >= 60) setRange({ from, to });
}

export function opts(height, series, { yMax, hooks = {} } = {}) {
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
export function chart(el, key, o, data) {
  const c = charts.get(el);
  if (c && c.key === key) { c.u.setData(data); return c.u; }
  c?.u.destroy();
  const u = new uPlot({ width: el.clientWidth || 300, ...o }, data, el);
  charts.set(el, { u, key });
  ro.observe(el);
  return u;
}

export function dropCharts(inside) {
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

export const line = (label, color, extra = {}) => ({ label, stroke: css(color), width: 1.5, value: (u, v) => fmtBytes(v), ...extra });

export function rssChart(el, s, height) {
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

export function spark(values) {
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
  const focused = document.activeElement?.closest?.("#apps-body tr")?.dataset.app; // survive the poll rebuild
  $("#apps-body").innerHTML = rows.length
    ? rows.map((r) => (has("processes") && r.name === OTHER_SMALL
      ? `<tr class="flat" title="Small apps aren't broken down by process">`
      : `<tr tabindex="0" data-app="${esc(r.name)}" aria-controls="app-detail" aria-expanded="${r.name === selectedApp}">`) + `
        <th scope="row" title="${esc(r.name)}">${esc(r.name)}</th>
        <td class="num">${fmtBytes(r.avg)}</td><td class="num">${fmtBytes(r.peak)}</td>
        <td class="num procs">${r.procs}</td><td>${spark(r.spark)}</td></tr>`).join("")
    : `<tr><td colspan="5" class="empty">No app data in this range.</td></tr>`;
  if (focused) document.querySelector(`#apps-body tr[data-app="${CSS.escape(focused)}"]`)?.focus();
  renderAppDetail(v);
}

function renderAppDetail(v) {
  const box = $("#app-detail"), el = $("#app-chart");
  box.hidden = !selectedApp;
  if (!selectedApp) return dropCharts(box);
  $("#app-title").textContent = selectedApp;
  el.setAttribute("aria-label", `Memory used by ${selectedApp} over the selected range`);
  rssChart(el, v.apps[selectedApp], 200);
  const tree = $("#app-tree");
  tree.hidden = !has("processes");
  if (tree.hidden) return;
  if (v.tree?.app === selectedApp) renderTree(tree, v.tree.ps);
  else tree.innerHTML = `<p class="empty">Loading processes…</p>`;
}

async function selectRow(tr) {
  if (!tr?.dataset.app) return;
  selectedApp = selectedApp === tr.dataset.app ? null : tr.dataset.app;
  renderApps(view);
  const app = selectedApp, v = view;
  if (!app || !has("processes") || v.tree?.app === app) return;
  try {
    const r = await net(src.processes(v.from, v.to, { step: v.step, app, top: "all" }));
    if (selectedApp !== app) return; // another click won
    if (view.tree?.app !== app) view.tree = { app, ps: r.processes };
    renderAppDetail(view);
  } catch (e) {
    console.error(e);
    if (selectedApp === app) $("#app-tree").innerHTML = `<p class="empty">Could not load processes.</p>`;
  }
}

function renderLeaks(v) {
  const box = $("#leaks");
  dropCharts(box);
  const leaks = v.leaks;
  if (!leaks.length) return empty(box, "No leaks detected in this range.");
  box.innerHTML = leaks.map((l, i) => `<article class="card">
      <h3>${esc(l.name)}</h3>
      <div class="stat">${plus(l.slopePerHour)}/h</div>
      <p class="meta">${plus(l.growth)} from ${fmtTime(l.startTs)} to ${fmtTime(l.endTs)} · fit R² <span class="mono">${l.r2.toFixed(2)}</span></p>
      ${l.culprits.length ? `<h4>Responsible process(es)</h4><ol>${l.culprits.map((c) => `<li><span>${procLink(c, `PID ${c.pid} ${shortCmd(c)}`)}${c.largest ? ` <span class="muted">largest grower</span>` : ""}</span><span class="num">${plus(c.growth)}</span></li>`).join("")}</ol>` : ""}
      <div class="chart" data-leak="${i}" role="figure" aria-label="Memory used by ${esc(l.name)} over the selected range"></div>
    </article>`).join("");
  leaks.forEach((l, i) => rssChart(box.querySelector(`[data-leak="${i}"]`), v.apps[l.name], 140));
}

function renderSpikes(v) {
  const box = $("#spikes");
  if (!v.spikes.length) return empty(box, "No spikes in this range.");
  box.innerHTML = [...v.spikes].reverse().map((s) => `<article class="card">
      <h3>${fmtTime(s.peakTs)}</h3>
      <div class="stat">${s.reason === "pressure" && s.rise <= 0 ? "Memory pressure" : plus(s.rise)}</div>
      <p class="meta">${s.reason === "pressure" ? "Memory pressure left normal" : "Used memory rose fast"}</p>
      ${s.causes.length
        ? `<ol aria-label="Top causes">${s.causes.map((c) => `<li><span>${c.pid != null
            ? `${procLink(c, `PID ${c.pid} ${shortCmd(c)}`)} <span class="muted">· ${esc(c.app ?? "?")}</span>`
            : esc(c.name)}</span><span class="num">${plus(c.growth)}</span></li>`).join("")}</ol>`
        : `<p class="empty">No single ${has("processes") ? "process" : "app"} grew. Likely system memory.</p>`}
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

// ---------- tabs + live poll ----------

function showTab(t, focus = false) {
  if (t === "live" && !has("live")) t = "overview";
  const changed = t !== tab;
  tab = t;
  for (const name of ["overview", "live"]) {
    const b = $(`#tab-${name}`), on = name === t;
    b.setAttribute("aria-selected", String(on));
    b.tabIndex = on ? 0 : -1;
    $(`#${name}`).hidden = !on;
  }
  if (focus) $(`#tab-${t}`).focus();
  const hash = t === "live" ? "#live" : "";
  if (location.hash !== hash) history.replaceState(null, "", hash || location.pathname + location.search);
  clearInterval(ticker);
  if (t === "live") { clearTimeout(timer); renderLive(); ticker = setInterval(tickLive, 1000); }
  if (changed) {
    if (t === "overview" && view) refresh(); // Overview stops polling while hidden
    poll();
  }
}

// Tabs and insights need meta.features, so they appear once meta is known (init or any refresh).
function enableLive() {
  const tabs = $("#tabs");
  if (!tabs.hidden || !has("live")) return;
  tabs.hidden = false;
  showTab(location.hash === "#live" ? "live" : "overview");
  if (tab !== "live") poll(); // showTab already polled when it switched to Live
}

// One /api/live poll feeds both the insights strip and the Live table: 2 s (fast) while the
// Live tab is shown and not paused, else 10 s for insights only. Nothing while the page is hidden.
async function poll() {
  clearTimeout(liveTimer);
  const my = ++liveGen;
  if (!has("live") || document.visibilityState !== "visible") return;
  const fast = tab === "live" && !isPaused();
  try {
    const d = await src.live({ fast });
    if (my !== liveGen) return;
    renderInsights(insights({ live: d, leaks: insightLeaks() }), d);
    if (fast) setLive(d);
  } catch (e) {
    if (my !== liveGen) return;
    console.error(e);
    if (fast) liveError(LOST);
  }
  liveTimer = setTimeout(poll, fast ? 2000 : 10000);
}

// Current-range app leaks, tagged with their top responsible process for the insight link.
function insightLeaks() {
  return (view?.leaks ?? []).map((l) => {
    const c = l.culprits[0];
    return c ? { ...l, pid: c.pid, start_ts: c.start_ts, id: c.id, app: l.name } : l;
  });
}

// ---------- wiring ----------

function bind() {
  document.querySelectorAll("[data-range]").forEach((b) => b.addEventListener("click", () => setRange({ preset: b.dataset.range })));
  const onCustom = () => {
    const from = new Date($("#from").value).getTime() / 1000, to = new Date($("#to").value).getTime() / 1000;
    if (from < to) setRange({ from: Math.floor(from), to: Math.floor(to) });
  };
  document.querySelectorAll("[role=tab]").forEach((b) => {
    b.addEventListener("click", () => showTab(b.id.slice(4)));
    b.addEventListener("keydown", (e) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) return;
      e.preventDefault();
      const t = e.key === "Home" ? "overview" : e.key === "End" ? "live" : tab === "live" ? "overview" : "live";
      showTab(t, true);
    });
  });
  addEventListener("hashchange", () => showTab(location.hash === "#live" ? "live" : "overview"));
  document.addEventListener("visibilitychange", poll);
  bindLive(poll);
  bindInsights();
  bindProcess();
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

let bound = false;
async function init() {
  if (!bound) { bind(); bound = true; }
  try {
    src = await loadSource();
  } catch (e) {
    console.error(e);
    if (e.local) { setStatus("RMS server not responding. Retrying…", true); return void setTimeout(init, 3000); }
    return setStatus(`Could not load data: ${e.message}`, true);
  }
  setStatus(null);
  meta = await src.meta().catch(() => null);
  if (!isLive()) range = { preset: "3d" }; // the demo's planted events span its 3 days
  enableLive();
  refresh();
}

init();
