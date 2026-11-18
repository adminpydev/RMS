// Process table over /api/live (or the demo replay). Polling lives in app.js; this module
// renders the latest snapshot and owns sort / search / pause state. Rows carry data-key,
// so the shared handler in process.js opens their dialog.
import { esc, fmtBytes, fmtCpu, fmtPct, procKey, statusCell } from "./util.js";

const MAX_ROWS = 200;
// Process table columns, shared by the main table and the apps sub-tables.
const PROC_COLS = [["pid", "PID", 1], ["name", "Process"], ["app", "App"], ["user", "User"], ["rss", "Memory", 1],
  ["share", "% RAM", 1], ["cpu", "CPU", 1], ["threads", "Threads", 1], ["status", "Status"]];
export const COLS = PROC_COLS.length;
// sortable: header cells carry data-sort and a button (main table only)
export const procHead = (sortable) => `<tr>${PROC_COLS.map(([k, label, num]) => `<th scope="col"${num ? ` class="num"` : ""}${
  sortable ? ` data-sort="${k}"><button type="button">${label}</button>` : `>${label}`}</th>`).join("")}</tr>`;
const $ = (s) => document.querySelector(s);

let data = null, at = 0, sort = { key: "rss", dir: -1 }, query = "", paused = false, error = null;

export const isPaused = () => paused;

export function setLive(d) { data = d; at = Date.now(); error = null; renderLive(); }
export function liveError(msg) { error = msg; tickLive(); }

// One process row; the apps table reuses it for its inline sub-tables.
export const procRow = (p) => `<tr tabindex="0" data-key="${esc(procKey(p))}">
  <td class="num">${esc(p.pid)}</td>
  <th scope="row" title="${esc(p.cmdline || p.name || "")}">${esc(p.name ?? "?")}</th>
  <td class="clip" title="${esc(p.app ?? "")}">${esc(p.app ?? "—")}</td>
  <td class="clip">${esc(p.user ?? "—")}</td>
  <td class="num">${fmtBytes(p.rss)}</td>
  <td class="num">${fmtPct(p.share)}</td>
  <td class="num">${fmtCpu(p.cpu)}</td>
  <td class="num">${esc(p.threads ?? "—")}</td>
  <td>${statusCell(p.status)}</td></tr>`;

// Comparator for sort = {key, dir: 1 | -1}; nulls last both ways.
export const cmpBy = (sort) => (a, b) => {
  const x = a[sort.key], y = b[sort.key];
  if (x == null || y == null) return (x == null) - (y == null);
  return (typeof x === "string" ? x.localeCompare(y) : x - y) * sort.dir;
};

// Search: q (already trimmed + lowercased) in name, cmdline, app, user or PID.
export const matches = (p, q) => !q || [p.name, p.cmdline, p.app, p.user, String(p.pid)].some((s) => s?.toLowerCase().includes(q));
export const getQuery = () => query.trim().toLowerCase();

export function renderLive() {
  const body = $("#live-body");
  document.querySelectorAll("#live-table th[data-sort]").forEach((th) => {
    th.setAttribute("aria-sort", th.dataset.sort === sort.key ? (sort.dir > 0 ? "ascending" : "descending") : "none");
  });
  tickLive();
  if (!data) return void (body.innerHTML = `<tr><td colspan="${COLS}" class="empty">Loading…</td></tr>`);
  const all = data.processes;
  if (!all.length) {
    $("#live-count").textContent = "";
    return void (body.innerHTML = `<tr><td colspan="${COLS}" class="empty">Collecting…</td></tr>`);
  }
  const q = getQuery();
  const hit = q ? all.filter((p) => matches(p, q)) : all;
  const rows = [...hit].sort(cmpBy(sort)).slice(0, MAX_ROWS);
  const unmatched = all.length - hit.length, more = hit.length - rows.length;
  $("#live-count").textContent = `Showing ${rows.length} of ${all.length} processes${
    unmatched ? ` · ${unmatched} not matching` : ""}${more ? ` · +${more} more` : ""}`;
  const focused = document.activeElement?.closest?.("#live-body tr")?.dataset.key; // survive rebuild
  body.innerHTML = rows.length ? rows.map(procRow).join("")
    : `<tr><td colspan="${COLS}" class="empty">No process matches “${esc(query)}”.</td></tr>`;
  if (focused) body.querySelector(`tr[data-key="${CSS.escape(focused)}"]`)?.focus({ preventScroll: true });
}

// "Updated N s ago" line; app.js calls it every second.
export function tickLive() {
  const el = $("#live-meta");
  if (error) return void (el.textContent = error);
  if (!data) return void (el.textContent = "");
  el.textContent = `${paused ? "Paused · updated" : "Updated"} ${Math.max(0, Math.round((Date.now() - at) / 1000))} s ago`;
}

export function bindLive(pauseChanged, searchChanged) {
  $("#live-table thead").innerHTML = procHead(true);
  document.querySelectorAll("#live-table th[data-sort] button").forEach((b) => b.addEventListener("click", () => {
    const k = b.closest("th").dataset.sort;
    // numbers start high-to-low, text A-Z
    sort = sort.key === k ? { key: k, dir: -sort.dir } : { key: k, dir: ["name", "app", "user", "status"].includes(k) ? 1 : -1 };
    renderLive();
  }));
  $("#live-search").addEventListener("input", (e) => { query = e.target.value; renderLive(); searchChanged(); });
  const pause = $("#live-pause");
  pause.addEventListener("click", () => {
    paused = !paused;
    pause.setAttribute("aria-pressed", String(paused));
    pause.textContent = paused ? "Resume" : "Pause";
    tickLive();
    pauseChanged();
  });
}
