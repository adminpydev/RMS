// Apps table: one row per app; click or Enter expands an inline sub-table of that app's
// processes (same columns as the process table). Expanded apps survive refreshes.
import { esc, fmtBytes, fmtPct } from "./util.js";
import { procRow, COLS } from "./live.js";

const MAX_SUB = 100;
const open = new Set();
let data = null;
const body = () => document.getElementById("apps-body");

export function setApps(d) { data = d; renderApps(); }

function sub(app) {
  const ps = data.processes.filter((p) => p.app === app).sort((a, b) => (b.rss ?? -1) - (a.rss ?? -1));
  const more = ps.length - MAX_SUB;
  return `<tr class="sub"><td colspan="4"><div class="table-wrap"><table class="apps">
    <thead><tr><th scope="col" class="num">PID</th><th scope="col">Process</th><th scope="col">App</th><th scope="col">User</th>
    <th scope="col" class="num">Memory</th><th scope="col" class="num">% RAM</th><th scope="col" class="num">CPU</th>
    <th scope="col" class="num">Threads</th><th scope="col">Status</th></tr></thead>
    <tbody>${ps.slice(0, MAX_SUB).map(procRow).join("")}${more > 0
      ? `<tr><td colspan="${COLS}" class="empty">+${more} more</td></tr>` : ""}</tbody></table></div></td></tr>`;
}

function renderApps() {
  const el = body();
  if (!data?.apps.length) return void (el.innerHTML = `<tr><td colspan="4" class="empty">Collecting…</td></tr>`);
  const f = document.activeElement, fApp = f?.closest?.("#apps-body tr[data-app]")?.dataset.app;
  const fKey = f?.closest?.("#apps-body tr[data-key]")?.dataset.key;
  el.innerHTML = data.apps.map((a) => `<tr data-app="${esc(a.app)}">
    <th scope="row"><button type="button" class="link toggle" aria-expanded="${open.has(a.app)}">${esc(a.app)}</button></th>
    <td class="num">${fmtBytes(a.rss)}</td><td class="num">${fmtPct(a.share)}</td><td class="num">${esc(a.procs)}</td></tr>${
    open.has(a.app) ? sub(a.app) : ""}`).join("");
  // keep keyboard focus across the rebuild
  if (fKey) el.querySelector(`tr[data-key="${CSS.escape(fKey)}"]`)?.focus({ preventScroll: true });
  else if (fApp) el.querySelector(`tr[data-app="${CSS.escape(fApp)}"] .toggle`)?.focus({ preventScroll: true });
}

export function bindApps() {
  body().addEventListener("click", (e) => {
    const row = e.target.closest("tr[data-app]");
    if (!row) return;
    const app = row.dataset.app;
    if (!open.delete(app)) open.add(app);
    renderApps();
    body().querySelector(`tr[data-app="${CSS.escape(app)}"] .toggle`)?.focus({ preventScroll: true });
  });
}
