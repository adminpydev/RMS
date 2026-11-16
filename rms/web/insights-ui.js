// Insights strip. insights() returns plain text; everything is escaped here.
// Items with a process carry data-key, so the shared handler in process.js opens its dialog.
import { esc, procKey } from "./util.js";

let last = "";

export function renderInsights(list) {
  const box = document.getElementById("insights");
  const sig = JSON.stringify(list);
  if (sig === last) return; // unchanged: keep DOM (and focus) as is
  last = sig;
  const focused = box.contains(document.activeElement) ? document.activeElement.dataset.key : null;
  box.innerHTML = list.length
    ? `<ul>${list.map((it) => `<li class="${it.severity === "warn" ? "warn" : "info"}"><span class="sr">${it.severity === "warn" ? "Warning:" : "Note:"}</span>${
        it.pid != null
          ? `<button type="button" class="link" data-key="${esc(procKey(it))}">${esc(it.text)}</button>`
          : `<span>${esc(it.text)}</span>`}</li>`).join("")}</ul>`
    : `<p class="calm">All calm. Nothing unusual right now.</p>`;
  if (focused) box.querySelector(`[data-key="${CSS.escape(focused)}"]`)?.focus();
}
