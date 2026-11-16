// Live tab: table over /api/live (or the demo replay). Polling lives in app.js; this module
// renders the latest snapshot and owns sort / search / pause state.
import { esc, fmtBytes, spark } from "./app.js";
import { openProcess, fmtCpu, procKey } from "./process.js";

const MAX_ROWS = 200;
const COLS = 9;
const $ = (s) => document.querySelector(s);

let data = null, at = 0, sort = { key: "rss", dir: -1 }, query = "", paused = false, error = null;
let onPause = () => {};

export const isPaused = () => paused;

export function setLive(d) { data = d; at = Date.now(); error = null; renderLive(); }
export function liveError(msg) { error = msg; tickLive(); }

const delta = (g) => (g == null ? "–" : `${g > 0 ? "+" : g < 0 ? "−" : ""}${fmtBytes(Math.abs(g))}`);

function cmp(a, b) {
  const x = a[sort.key], y = b[sort.key];
  if (x == null || y == null) return (x == null) - (y == null); // nulls last both ways
  return (typeof x === "string" ? x.localeCompare(y) : x - y) * sort.dir;
}

export function renderLive() {
  const body = $("#live-body");
  document.querySelectorAll("#live-table th[data-sort]").forEach((th) => {
    th.setAttribute("aria-sort", th.dataset.sort === sort.key ? (sort.dir > 0 ? "ascending" : "descending") : "none");
  });
  tickLive();
  if (!data) return void (body.innerHTML = `<tr><td colspan="${COLS}" class="empty">Loading…</td></tr>`);
  const all = data.processes;
  if (!data.system && !all.length) {
    $("#live-count").textContent = "";
    return void (body.innerHTML = `<tr><td colspan="${COLS}" class="empty">Collecting…</td></tr>`);
  }
  const q = query.trim().toLowerCase();
  const hit = q ? all.filter((p) => [p.name, p.cmdline, p.app, String(p.pid)].some((s) => s?.toLowerCase().includes(q))) : all;
  const rows = [...hit].sort(cmp).slice(0, MAX_ROWS);
  const unmatched = all.length - hit.length, more = hit.length - rows.length;
  $("#live-count").textContent = `Showing ${rows.length} of ${all.length} processes${
    unmatched ? ` · ${unmatched} not matching` : ""}${more ? ` · +${more} more` : ""}`;
  const focused = document.activeElement?.closest?.("#live-body tr")?.dataset.key; // survive the rebuild
  body.innerHTML = rows.length ? rows.map((p) => `<tr tabindex="0" data-key="${esc(procKey(p))}">
      <td class="num">${p.pid}</td>
      <th scope="row" title="${esc(p.cmdline ?? p.name)}">${esc(p.name ?? "?")}</th>
      <td class="clip" title="${esc(p.app ?? "")}">${esc(p.app ?? "–")}</td>
      <td class="clip">${esc(p.user ?? "–")}</td>
      <td class="num">${fmtBytes(p.rss)}</td>
      <td class="num ${p.growth_5m > 0 ? "up" : p.growth_5m < 0 ? "down" : ""}">${delta(p.growth_5m)}</td>
      <td class="num">${fmtCpu(p.cpu)}</td>
      <td class="num">${p.threads ?? "–"}</td>
      <td>${p.spark?.length ? spark(p.spark) : ""}</td></tr>`).join("")
    : `<tr><td colspan="${COLS}" class="empty">No process matches “${esc(query)}”.</td></tr>`;
  if (focused) body.querySelector(`tr[data-key="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
}

// "Updated N s ago" line; app.js calls this every second while the tab is shown.
export function tickLive() {
  const el = $("#live-meta");
  if (error) return void (el.textContent = error);
  if (!data) return void (el.textContent = "");
  const parts = [];
  if (data.simulated) parts.push("Simulated replay of sample data");
  parts.push(`${paused ? "Paused · updated" : "Updated"} ${Math.max(0, Math.round((Date.now() - at) / 1000))} s ago`);
  if (data.resolution) parts.push(`${data.resolution} s resolution`);
  el.textContent = parts.join(" · ");
}

function open(tr) {
  const p = data?.processes.find((r) => procKey(r) === tr?.dataset.key);
  if (p) openProcess(p, tr);
}

export function bindLive(pauseChanged) {
  onPause = pauseChanged;
  document.querySelectorAll("#live-table th[data-sort] button").forEach((b) => b.addEventListener("click", () => {
    const k = b.closest("th").dataset.sort;
    // numbers start high-to-low, text A-Z
    sort = sort.key === k ? { key: k, dir: -sort.dir } : { key: k, dir: ["name", "app", "user"].includes(k) ? 1 : -1 };
    renderLive();
  }));
  $("#live-search").addEventListener("input", (e) => { query = e.target.value; renderLive(); });
  const pause = $("#live-pause");
  pause.addEventListener("click", () => {
    paused = !paused;
    pause.setAttribute("aria-pressed", String(paused));
    pause.textContent = paused ? "Resume" : "Pause";
    tickLive();
    onPause();
  });
  const body = $("#live-body");
  body.addEventListener("click", (e) => open(e.target.closest("tr")));
  body.addEventListener("keydown", (e) => {
    if (e.key !== "Enter" && e.key !== " ") return;
    e.preventDefault();
    open(e.target.closest("tr"));
  });
}
