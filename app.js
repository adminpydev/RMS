// RMS page orchestrator: header, demo banner and the single /api/live poll that feeds the
// system panel, insights, apps and process tables and the open process dialog.
// Data comes from LiveSource (local server) or DemoSource (sample.json replay).
import { loadSource, outdated } from "./sources.js";
import { insights } from "./insights.js";
import { esc, fmtBytes } from "./util.js";
import { renderSystem } from "./system.js";
import { setApps, renderApps, bindApps } from "./apps.js";
import { setLive, liveError, tickLive, isPaused, bindLive } from "./live.js";
import { renderInsights } from "./insights-ui.js";
import { bindProcess, refreshProcess } from "./process.js";

const $ = (s) => document.querySelector(s);
const POLL_MS = 2000, PAUSED_MS = 10000;
const LOST = "Lost connection to RMS. Retrying…";
const OLD = "This RMS server is older than the dashboard. Update with: pipx install --force git+https://github.com/adminpydev/RMS";
const OS = { darwin: "macOS", linux: "Linux", win32: "Windows" };

let src, meta, last = null, timer = null, gen = 0;

function setStatus(msg) {
  const el = $("#status");
  el.hidden = !msg;
  el.textContent = msg ?? "";
}

function renderHeader() {
  const badge = $("#mode");
  badge.hidden = false;
  // ponytail: demo and local look identical on purpose (user request); the host line names the machine
  badge.textContent = "Live";
  badge.classList.add("live");
  $("#host").textContent = `${meta.host} · ${OS[meta.os] ?? meta.os} · ${fmtBytes(meta.total_ram)} RAM`;
}

// One /api/live fetch per tick: every 2 s while visible. Paused freezes both tables; the
// system panel, insights and an open dialog keep updating every 10 s. Hidden: no polling
// (visibilitychange / Pause / Resume restart it).
async function poll() {
  clearTimeout(timer);
  const my = ++gen;
  if (document.visibilityState !== "visible") return;
  const paused = isPaused();
  try {
    const d = await src.live();
    if (my !== gen) return; // a newer poll started meanwhile
    last = d;
    setStatus(null);
    renderSystem(d.system);
    renderInsights(insights({ live: d }), d);
    if (paused) liveError(null); // tables stay frozen; just clear a stale error
    else { setApps(d); setLive(d); }
    refreshProcess();
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
  if (outdated(meta)) {
    document.body.innerHTML = `<main class="wrap"><p class="old">${esc(OLD)}</p></main>`;
    return;
  }
  setStatus(null);
  renderHeader();
  $("#main").hidden = false;
  bindProcess(src, () => last);
  bindApps();
  bindLive(poll, renderApps);
  document.addEventListener("visibilitychange", poll);
  setInterval(tickLive, 1000);
  poll();
}

init();
