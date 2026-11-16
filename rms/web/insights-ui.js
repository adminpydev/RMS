// Insights strip above the tabs. insights() returns plain text; everything is escaped here.
import { esc } from "./app.js";
import { openProcess, procKey } from "./process.js";

let items = [], last = "", snapshot = null;

// list: insights() output; live: the snapshot it came from (for live-only processes).
export function renderInsights(list, live) {
  snapshot = live;
  const box = document.getElementById("insights");
  box.hidden = false;
  const sig = JSON.stringify(list);
  if (sig === last) return; // unchanged: keep DOM (and focus) as is
  last = sig;
  items = list;
  const focused = box.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  box.innerHTML = list.length
    ? `<ul>${list.map((it, i) => `<li class="${it.severity === "warn" ? "warn" : "info"}"><span class="sr">${it.severity === "warn" ? "Warning:" : "Note:"}</span>${
        it.pid != null
          ? `<button type="button" class="link" data-insight="${i}" data-key="${i}:${esc(procKey(it))}">${esc(it.text)}</button>`
          : `<span>${esc(it.text)}</span>`}</li>`).join("")}</ul>`
    : `<p class="calm">All calm. Nothing unusual right now.</p>`;
  if (focused) box.querySelector(`[data-key="${CSS.escape(focused)}"]`)?.focus();
}

export function bindInsights() {
  document.getElementById("insights").addEventListener("click", (e) => {
    const b = e.target.closest("[data-insight]");
    const it = b && items[+b.dataset.insight];
    if (!it) return;
    if (it.id != null) return openProcess({ id: it.id, pid: it.pid, start_ts: it.start_ts }, b);
    const row = snapshot?.processes.find((p) => p.pid === it.pid && p.start_ts === it.start_ts);
    openProcess(row ?? { id: null, pid: it.pid, start_ts: it.start_ts, app: it.app }, b);
  });
}
