// Process detail dialog. Any element with data-key="pid:start_ts" (table rows, insight items,
// parent/children links) opens it; links inside the dialog replace its content, and focus
// returns to the original opener on close.
import { esc, fmtBytes, fmtCpu, fmtTime, procKey, lineChart } from "./util.js";

let src = null, snapshot = () => null;
let opener = null, openerKey = null, openerBox = null, token = 0, retry = null;
const dlg = () => document.getElementById("proc");

const link = (p, text) => `<button type="button" class="link" data-key="${esc(procKey(p))}">${esc(text)}</button>`;
const title = (html) => { document.getElementById("proc-title").innerHTML = html; };
const facts = (rows) => `<dl class="facts">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
const cmdBlock = (cmd) => `<h3>Command</h3>${cmd
  ? `<pre class="cmd-full">${esc(cmd)}</pre><p class="copy-row"><button type="button" class="btn" data-copy>Copy command</button> <span class="muted" role="status" data-copied></span></p>`
  : `<p class="empty">Not available (no permission to read it).</p>`}`;

export async function openProcess(pid, start_ts, from, again = false) {
  const d = dlg();
  retry = null;
  if (!d.open) {
    opener = from ?? document.activeElement;
    openerKey = opener?.dataset?.key ?? null;
    openerBox = opener?.closest?.("[id]") ?? null; // rebuilt openers are looked up only in here
    d.showModal();
  }
  const my = ++token, body = document.getElementById("proc-body");
  const row = snapshot()?.processes.find((r) => r.pid === pid && r.start_ts === start_ts);
  if (!again) {
    title(esc(row ? `${row.name} (PID ${pid})` : `PID ${pid}`));
    body.innerHTML = `<p class="empty">Loading…</p>`;
  }
  let p;
  try {
    p = await src.process(pid, start_ts);
  } catch (e) {
    if (my !== token) return;
    if (e.status === 404) {
      body.innerHTML = `<p class="empty">This process is no longer in the last 30 minutes of data.</p>`;
    } else {
      body.innerHTML = `<p class="empty">Could not load, retrying…</p>`;
      retry = { pid, start_ts };
    }
    return;
  }
  if (my !== token || !d.open) return;
  title(`${esc(`${p.name} (PID ${p.pid})`)}${p.alive ? "" : ` <span class="badge">Exited</span>`}`);
  // parent comes as {pid, name}; its start_ts is found among the processes alive now
  const par = p.parent && snapshot()?.processes.find((r) => r.pid === p.parent.pid);
  const parent = !p.parent ? "–" : par ? link(par, `PID ${par.pid} ${par.name ?? ""}`)
    : `<span class="mono">PID ${esc(p.parent.pid)}</span> ${esc(p.parent.name ?? "")}`;
  const children = p.children.length ? p.children.map((c) => link(c, `PID ${c.pid} ${c.name}`)).join(", ") : "None";
  const lastRss = p.series[p.series.length - 1]?.rss;
  body.innerHTML = `${facts([
    ["PID", `<span class="mono">${esc(p.pid)}</span>`], ["Parent", parent], ["Children", children],
    ["App", esc(p.app ?? "–")], ["User", esc(p.user ?? "Unknown")], ["Started", fmtTime(p.start_ts)],
    [p.alive ? "Memory" : "Last memory", fmtBytes(lastRss)]])}
    ${cmdBlock(p.cmdline)}
    <h3>Memory</h3>${lineChart(p.series, "rss", fmtBytes, "Memory used by this process over the last 30 minutes")}
    <h3>CPU</h3>${lineChart(p.series, "cpu", fmtCpu, "CPU used by this process over the last 30 minutes")}`;
}

// app.js calls this after every poll: a dialog whose load failed (not a 404) tries again.
export function retryProcess() {
  if (retry && dlg().open) openProcess(retry.pid, retry.start_ts, null, true);
}

function openFrom(el) {
  const [pid, start_ts] = el.dataset.key.split(":").map(Number);
  openProcess(pid, start_ts, el);
}

// source: LiveSource/DemoSource; getSnapshot: latest /api/live body (row names, parent lookup).
export function bindProcess(source, getSnapshot) {
  src = source;
  snapshot = getSnapshot;
  const d = dlg();
  const OPENERS = "#live-body tr[data-key], #insights [data-key], #proc-body [data-key]";
  document.addEventListener("click", (e) => {
    const el = e.target.closest?.(OPENERS);
    if (el) openFrom(el);
  });
  document.addEventListener("keydown", (e) => {
    const el = e.target.closest?.("#live-body tr[data-key]");
    if (!el || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    openFrom(el);
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
    retry = null;
    // the opener may have been rebuilt by a poll while the dialog was open
    const back = opener?.isConnected ? opener
      : openerKey && openerBox?.querySelector(`[data-key="${CSS.escape(openerKey)}"]`);
    (back || document.getElementById("live-search")).focus(); // opener gone: back to search
    opener = openerKey = openerBox = null;
  });
}
