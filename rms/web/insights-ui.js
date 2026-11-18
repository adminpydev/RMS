// Insights strip. insights() returns plain text; everything is escaped here.
// Items with a process carry data-key, so the shared handler in process.js opens its dialog.
// The strip is rebuilt (and announced via #insights-sr) only when the set of insights changes
// (kind + process); live numbers in an unchanged set are updated in place, silently.
import { esc, procKey } from "./util.js";

const CALM = "All calm. Nothing unusual right now.";
let last = "";

export function renderInsights(list, live) {
  const box = document.getElementById("insights");
  const calm = !live?.system ? "Collecting…" : list.length ? null : CALM;
  const sig = calm ?? list.map((it) => `${it.kind}:${it.pid ?? ""}:${it.start_ts ?? ""}:${it.app ?? ""}`).join("|");
  if (sig === last) {
    // same insights: refresh text in place (keeps DOM, focus, and the announcer quiet)
    box.querySelectorAll("li > :not(.sr)").forEach((el, i) => { if (list[i]) el.textContent = list[i].text; });
    return;
  }
  last = sig;
  const focused = box.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  box.innerHTML = calm ? `<p class="calm">${esc(calm)}</p>`
    : `<ul>${list.map((it) => `<li class="${it.severity === "warn" ? "warn" : "info"}"><span class="sr">${it.severity === "warn" ? "Warning:" : "Note:"}</span>${
        it.pid != null
          ? `<button type="button" class="link" data-key="${esc(procKey(it))}">${esc(it.text)}</button>`
          : `<span>${esc(it.text)}</span>`}</li>`).join("")}</ul>`;
  document.getElementById("insights-sr").textContent = calm ?? list.map((it) => it.text).join(" ");
  if (focused) box.querySelector(`[data-key="${CSS.escape(focused)}"]`)?.focus();
}
