// System panel: stacked SVG bar of the memory breakdown with a legend list, swap and pressure.
import { esc, fmtBytes, fmtPct } from "./util.js";

const LABELS = { wired: "Wired", active: "Active", inactive: "Inactive", compressed: "Compressed",
                 used: "Used", cached: "Cached", free: "Free" };
// macOS kern.memorystatus_vm_pressure_level: 1 normal, 2 warning, 4 critical
const PRESSURE = { 1: ["Normal", "ok"], 2: ["Warning", "warn"], 4: ["Critical", "crit"] };

export function renderSystem(sys) {
  const el = document.getElementById("system");
  if (!sys) return void (el.innerHTML = `<p class="empty">Collecting…</p>`);
  const parts = Object.entries(sys.breakdown ?? {}).filter(([, v]) => v != null);
  const sum = parts.reduce((s, [, v]) => s + v, 0) || 1;
  let x = 0;
  const rects = parts.map(([k, v]) => {
    const w = (v / sum) * 100, r = `<rect class="seg-${esc(k)}" x="${x.toFixed(2)}" width="${w.toFixed(2)}" height="10"><title>${
      esc(LABELS[k] ?? k)}: ${fmtBytes(v)} (${fmtPct(v / sys.total)})</title></rect>`;
    x += w;
    return r;
  }).join("");
  const [plabel, pcls] = PRESSURE[sys.pressure] ?? ["—", ""];
  el.innerHTML = `<p class="sys-head"><span><span class="num">${fmtBytes(sys.used)}</span> of <span class="num">${fmtBytes(sys.total)}</span> used</span>
    <span>Swap <span class="num">${fmtBytes(sys.swap_used)}</span> / <span class="num">${fmtBytes(sys.swap_total)}</span></span>
    <span>Pressure ${pcls ? `<i class="sw ${pcls}"></i>` : ""}${plabel}</span></p>
    <svg class="stack" viewBox="0 0 100 10" preserveAspectRatio="none" aria-hidden="true">${rects}</svg>
    <ul class="legend">${parts.map(([k, v]) => `<li><i class="sw seg-${esc(k)}"></i>${esc(LABELS[k] ?? k)}
      <span class="num">${fmtBytes(v)}</span> <span class="muted">${fmtPct(v / sys.total)}</span></li>`).join("")}</ul>`;
}
