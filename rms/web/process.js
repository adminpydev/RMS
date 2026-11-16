// Process tree (Top apps expansion) and the process detail dialog.
// Shared helpers come from app.js (ES module cycle: only used at call time, never at load).
import { topApps } from "./analytics.js";
import { src, span, esc, fmtBytes, fmtTime, spark, chart, opts, line, rssChart, dropCharts } from "./app.js";

const TREE_MAX = 100;
const LIVE_ONLY = "Not recorded in history (small or short-lived process)";

export const fmtCpu = (c) => (c == null ? "–" : `${(c / 10).toFixed(1)}%`);
export const procKey = (p) => `${p.pid}:${p.start_ts}`;

// "Google Chrome Helper (Renderer) --type=renderer …": executable basename plus args.
// Paths may contain spaces, so the basename is taken before the first " -" option.
export function shortCmd(p, n = 60) {
  const c = p.cmdline;
  if (!c) return p.name ?? "?";
  const opt = c.indexOf(" -"), head = opt < 0 ? c : c.slice(0, opt);
  const s = c.slice(head.lastIndexOf("/") + 1);
  if (s.length <= n) return s;
  const sp = s.lastIndexOf(" ", n - 1); // never split a token (e.g. a redacted ***)
  return `${sp > 0 ? s.slice(0, sp) : s.slice(0, n - 1)}…`;
}

// Link-styled button that opens the detail dialog for a stored process.
export const procLink = (p, text) =>
  `<button type="button" class="link" data-open-id="${esc(p.id)}" data-key="${esc(procKey(p))}">${esc(text)}</button>`;

// ---------- tree ----------

// ps: /api/processes `processes` map for one app. Children nest under the parent found by
// ppid within the same app (latest start_ts per pid, parent must start first); others are roots.
export function renderTree(box, ps) {
  const stats = new Map(topApps(Object.fromEntries(Object.entries(ps).map(([id, p]) => [id, p.series])))
    .map((r) => [r.name, r]));
  const rows = Object.entries(ps).map(([id, p]) => ({ ...p, id, ...stats.get(id), name: p.name }));
  if (!rows.length) return void (box.innerHTML = `<p class="empty">No processes recorded for this app in this range.</p>`);
  const byPid = new Map();
  for (const r of [...rows].sort((a, b) => a.start_ts - b.start_ts)) byPid.set(r.pid, r);
  const kids = new Map(), roots = [];
  for (const r of rows) {
    const par = byPid.get(r.ppid);
    if (par && par !== r && par.start_ts <= r.start_ts) {
      if (!kids.has(par.id)) kids.set(par.id, []);
      kids.get(par.id).push(r);
    } else roots.push(r);
  }
  const out = [], seen = new Set();
  const walk = (list, d) => {
    for (const r of list.sort((a, b) => b.avg - a.avg)) {
      if (seen.has(r.id)) continue;
      seen.add(r.id);
      out.push([r, d]);
      walk(kids.get(r.id) ?? [], d + 1);
    }
  };
  walk(roots, 0);
  const shown = out.slice(0, TREE_MAX);
  box.innerHTML = `<div class="table-wrap"><table class="apps tree">
    <thead><tr><th scope="col" class="num">PID</th><th scope="col">Process</th><th scope="col" class="cmdcol">Command</th><th scope="col">User</th><th scope="col" class="num">Avg</th><th scope="col" class="num">Peak</th><th scope="col">Trend</th></tr></thead>
    <tbody>${shown.map(([r, d]) => `<tr tabindex="0" data-open-id="${esc(r.id)}" data-key="${esc(procKey(r))}">
      <td class="num">${r.pid}</td>
      <th scope="row" style="--d:${d}"><span class="${d ? "child" : ""}" title="${esc(r.name)}">${esc(r.name)}</span></th>
      <td class="cmd cmdcol" title="${esc(r.cmdline ?? "")}">${esc(r.cmdline ?? "–")}</td>
      <td>${esc(r.user ?? "–")}</td>
      <td class="num">${fmtBytes(r.avg)}</td><td class="num">${fmtBytes(r.peak)}</td><td>${spark(r.spark)}</td></tr>`).join("")}</tbody>
  </table></div>${out.length > shown.length ? `<p class="hint">+${out.length - shown.length} more</p>` : ""}`;
}

// ---------- detail dialog ----------

let opener = null, openerKey = null, token = 0;
const dlg = () => document.getElementById("proc");

// ref: {id} for a stored process, or a live row ({id: null, pid, start_ts, ...}).
export async function openProcess(ref, from) {
  const d = dlg();
  if (!d.open) {
    opener = from ?? document.activeElement;
    openerKey = opener?.dataset?.key ?? null;
    d.showModal();
  }
  const my = ++token, body = document.getElementById("proc-body");
  dropCharts(body);
  if (ref.id == null) return renderLiveOnly(ref);
  title(ref.name ? `${ref.name} (PID ${ref.pid})` : "Process");
  body.innerHTML = `<p class="empty">Loading…</p>`;
  let p;
  try {
    const [f, t] = span();
    p = await src.process(ref.id, f, t);
  } catch (e) {
    if (my !== token) return;
    // a live row may outlive its pruned history; fall back to what Live knows
    if (ref.pid != null && ref.rss != null) return renderLiveOnly(ref);
    return void (body.innerHTML = `<p class="empty">Not found in history.</p>`);
  }
  if (my !== token || !d.open) return;
  title(`${p.name} (PID ${p.pid})`);
  const par = p.parent;
  const parent = !par ? "–" : par.id != null ? procLink(par, `PID ${par.pid} ${par.name ?? ""}`)
    : `<span class="mono">PID ${par.pid}</span>${par.name ? ` ${esc(par.name)}` : ""} <span class="muted">(not recorded)</span>`;
  const children = p.children.length ? p.children.map((c) => procLink(c, `PID ${c.pid} ${c.name}`)).join(", ") : "None";
  body.innerHTML = `${facts([
    ["PID", `<span class="mono">${p.pid}</span>`], ["Parent", parent], ["Children", children],
    ["App", esc(p.app ?? "–")], ["User", esc(p.user ?? "Unknown")], ["Started", fmtTime(p.start_ts)],
    ["First seen", p.first_seen != null ? fmtTime(p.first_seen) : "–"],
    ["Last seen", p.last_seen != null ? fmtTime(p.last_seen) : "–"]])}
    ${cmdBlock(p.cmdline)}
    <h3>Memory</h3><div class="chart" id="proc-mem" role="figure" aria-label="Memory used by this process over the selected range"></div>
    <h3>CPU</h3><div class="chart" id="proc-cpu" role="figure" aria-label="CPU used by this process over the selected range"></div>`;
  rssChart(document.getElementById("proc-mem"), p.series, 160);
  const o = opts(160, [line("CPU", "--compressed", { value: (u, v) => fmtCpu(v) })]);
  o.axes[1] = { ...o.axes[1], size: 60, values: (u, v) => v.map((x) => `${+(x / 10).toFixed(1)}%`) };
  o.scales.y.range = (u, mn, mx) => [0, Math.max(10, (mx ?? 0) * 1.08)];
  chart(document.getElementById("proc-cpu"), "cpu", o, [p.series.map((s) => s.ts), p.series.map((s) => s.cpu)]);
}

function renderLiveOnly(r) {
  title(`${r.name ?? "Process"} (PID ${r.pid})`);
  document.getElementById("proc-body").innerHTML = `<p class="note">${LIVE_ONLY}</p>
    ${facts([["PID", `<span class="mono">${r.pid}</span>`], ["Parent", r.ppid != null ? `<span class="mono">PID ${r.ppid}</span>` : "–"],
             ["App", esc(r.app ?? "–")], ["User", esc(r.user ?? "Unknown")],
             ["Started", r.start_ts != null ? fmtTime(r.start_ts) : "–"],
             ["Memory", r.rss != null ? fmtBytes(r.rss) : "–"], ["CPU", fmtCpu(r.cpu)]])}
    ${cmdBlock(r.cmdline)}
    ${r.spark?.length ? `<h3>Last 10 min</h3><div class="spark-lg">${spark(r.spark)}</div>` : ""}`;
}

const title = (t) => { document.getElementById("proc-title").textContent = t; };
const facts = (rows) => `<dl class="facts">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
const cmdBlock = (cmd) => `<h3>Command</h3>${cmd
  ? `<pre class="cmd-full">${esc(cmd)}</pre><p class="copy-row"><button type="button" class="btn" data-copy>Copy command</button> <span class="muted" role="status" data-copied></span></p>`
  : `<p class="empty">Not available (no permission to read it).</p>`}`;

export function bindProcess() {
  const d = dlg();
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-open-id]");
    if (el) openProcess({ id: el.dataset.openId }, el);
  });
  document.addEventListener("keydown", (e) => {
    const el = e.target.closest?.("tr[data-open-id]");
    if (!el || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    openProcess({ id: el.dataset.openId }, el);
  });
  d.addEventListener("click", async (e) => {
    if (e.target === d) return d.close(); // backdrop
    if (!e.target.closest("[data-copy]")) return;
    const msg = d.querySelector("[data-copied]");
    try {
      await navigator.clipboard.writeText(d.querySelector(".cmd-full").textContent);
      msg.textContent = "Copied";
    } catch {
      getSelection().selectAllChildren(d.querySelector(".cmd-full")); // let the user copy by hand
      msg.textContent = "Copy failed. Text selected, press Ctrl+C or ⌘C.";
    }
  });
  d.addEventListener("close", () => {
    token++;
    dropCharts(d);
    // the opener may have been rebuilt by a poll while the dialog was open
    const back = opener?.isConnected ? opener
      : openerKey && document.querySelector(`[data-key="${CSS.escape(openerKey)}"]`);
    back?.focus();
    opener = openerKey = null;
  });
}
