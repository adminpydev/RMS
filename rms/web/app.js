// RMS dashboard orchestrator: header, demo banner, system line and the single /api/live poll.
// Data comes from LiveSource (local server) or DemoSource (sample.json replay).
import { loadSource, outdated } from "./sources.js";
import { insights } from "./insights.js";
import { esc, fmtBytes } from "./util.js";
import { setLive, liveError, tickLive, isPaused, bindLive } from "./live.js";
import { renderInsights } from "./insights-ui.js";
import { bindProcess, retryProcess } from "./process.js";

const $ = (s) => document.querySelector(s);
const POLL_MS = 2000, PAUSED_MS = 10000;
const LOST = "Lost connection to RMS. Retrying…";
const OLD = "This RMS server is older than the dashboard. Update with: pipx install --force git+https://github.com/adminpydev/RMS";
const OS = { darwin: "macOS", linux: "Linux", win32: "Windows" };
// macOS kern.memorystatus_vm_pressure_level: 1 normal, 2 warning, 4 critical
const PRESSURE = { 1: ["normal", "ok"], 2: ["warning", "warn"], 4: ["critical", "crit"] };

let src, meta, last = null, timer = null, gen = 0;

function setStatus(msg) {
  const el = $("#status");
  el.hidden = !msg;
  el.textContent = msg ?? "";
}

function renderHeader() {
  const badge = $("#mode");
  badge.hidden = false;
  badge.textContent = meta.mode === "live" ? "Live" : "Demo";
  badge.classList.toggle("live", meta.mode === "live");
  $("#banner").hidden = meta.mode !== "demo";
  $("#host").textContent = `${meta.host} · ${OS[meta.os] ?? meta.os} · ${fmtBytes(meta.total_ram)} RAM`;
}

function renderSystem(sys) {
  const el = $("#system");
  if (!sys) return void (el.innerHTML = `<span class="muted">Collecting first sample…</span>`);
  const frac = Math.min(1, Math.max(0, sys.used / sys.total));
  const [plabel, pcls] = PRESSURE[sys.pressure] ?? [];
  el.innerHTML = `<span class="sys-used"><span class="num">${fmtBytes(sys.used)}</span> of <span class="num">${fmtBytes(sys.total)}</span> used</span>
    <svg class="bar" viewBox="0 0 100 6" preserveAspectRatio="none" role="img" aria-label="${Math.round(frac * 100)}% of memory used"><rect class="track" width="100" height="6" rx="3"/><rect class="fill ${pcls ?? ""}" width="${(frac * 100).toFixed(1)}" height="6" rx="3"/></svg>
    <span>Swap <span class="num">${fmtBytes(sys.swap_used)}</span></span>
    ${plabel ? `<span>Pressure <i class="sw ${pcls}"></i>${plabel}</span>` : ""}`;
}

// One /api/live fetch feeds the system line, insights and table: every 2 s (fast: 2 s
// samples) while visible. Paused freezes only the table: system line and insights keep
// polling every 10 s without fast (server falls back to 10 s samples). Hidden: no polling
// (visibilitychange / Pause / Resume restart it).
async function poll() {
  clearTimeout(timer);
  const my = ++gen;
  if (document.visibilityState !== "visible") return;
  const paused = isPaused();
  try {
    const d = await src.live({ fast: !paused });
    if (my !== gen) return; // a newer poll started meanwhile
    last = d;
    setStatus(null);
    renderSystem(d.system);
    renderInsights(insights({ live: d }), d);
    if (paused) liveError(null); // table stays frozen; just clear a stale error
    else setLive(d);
    retryProcess();
  } catch (e) {
    if (my !== gen) return;
    console.error(e);
    liveError(LOST);
  }
  timer = setTimeout(poll, paused ? PAUSED_MS : POLL_MS);
}

async function init() {
  try {
    src = await loadSource();
    meta = await src.meta();
  } catch (e) {
    console.error(e);
    if (e.local) { setStatus("RMS server not responding. Retrying…"); return void setTimeout(init, 3000); }
    return setStatus(`Could not load data: ${e.message}`);
  }
  if (outdated(meta.version) || !meta.features?.includes("live")) {
    document.body.innerHTML = `<main class="wrap"><p class="old">${esc(OLD)}</p></main>`;
    return;
  }
  setStatus(null);
  renderHeader();
  $("#main").hidden = false;
  bindProcess(src, () => last);
  bindLive(poll);
  document.addEventListener("visibilitychange", poll);
  setInterval(tickLive, 1000);
  poll();
}

init();
