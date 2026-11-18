// Process detail dialog. Any element with data-key="pid:start_ts" (table rows, insight items,
// parent/children links) opens it; links inside the dialog replace its content, and focus
// returns to the original opener on close. app.js calls refreshProcess() on every poll tick.
import { esc, fmtBytes, fmtCpu, fmtNum, fmtSecs, fmtTime, procKey, statusCell } from "./util.js";

let src = null, snapshot = () => null;
let opener = null, openerKey = null, openerBox = null, token = 0, cur = null, lastHtml = "", copied = "";
const dlg = () => document.getElementById("proc");
const ROWS = "#live-body tr[data-key], #apps-body tr[data-key]";

const link = (p, text) => `<button type="button" class="link" data-key="${esc(procKey(p))}">${esc(text)}</button>`;
const val = (v, f = esc) => (v == null || v === "" ? "—" : f(v));
const facts = (rows) => `<dl class="facts">${rows.map(([k, v]) => `<dt>${k}</dt><dd>${v}</dd>`).join("")}</dl>`;
const section = (h, rows) => `<h3>${h}</h3>${facts(rows)}`;
const ports = (ps) => (ps == null ? "—" : !ps.length ? "None"
  : ps.map((p) => `<span class="mono">${esc(p.port)}/${esc(p.proto)}</span> <span class="muted">${esc(p.addr)}</span>`).join("<br>"));

const MAX_KIDS = 50; // launchd has hundreds
const kids = (cs) => (!cs?.length ? "None" : cs.slice(0, MAX_KIDS).map((c) => link(c, `PID ${c.pid} ${c.name ?? ""}`)).join(", ")
  + (cs.length > MAX_KIDS ? ` <span class="muted">+${cs.length - MAX_KIDS} more</span>` : ""));

function render(p) {
  const row = snapshot()?.processes.find((r) => r.pid === p.pid && r.start_ts === p.start_ts);
  const cmd = p.cmdline
    ? `<pre class="cmd-full">${esc(p.cmdline)}</pre><p class="copy-row"><button type="button" class="btn" data-copy>Copy command</button> <span class="muted" role="status" data-copied>${esc(copied)}</span></p>`
    : `<p class="empty">Not available.</p>`;
  return `${p.restricted ? `<p class="note">Limited details — owned by another user or the system.</p>` : ""}
    ${facts([["App", val(p.app)], ["User", val(p.user)], ["Started", val(p.start_ts, fmtTime)], ["Status", statusCell(p.status)]])}
    ${section("Memory", [["RSS", fmtBytes(p.rss)], ["Unique", fmtBytes(p.uss)], ["Virtual", fmtBytes(p.vms)],
      ["Page faults", fmtNum(p.pfaults)], ["Page-ins", fmtNum(p.pageins)]])}
    ${section("CPU", [["Now", fmtCpu(row?.cpu)], ["User time", fmtSecs(p.cpu_user)], ["System time", fmtSecs(p.cpu_system)],
      ["Threads", fmtNum(p.threads)]])}
    ${section("Files &amp; network", [["Open files", fmtNum(p.open_files)], ["Listening ports", ports(p.ports)]])}
    ${section("Location", [["Program", val(p.exe)], ["Working folder", val(p.cwd)]])}
    <h3>Command</h3>${cmd}
    ${section("Family", [["Parent", p.parent ? link(p.parent, `PID ${p.parent.pid} ${p.parent.name ?? ""}`) : "—"],
      ["Children", kids(p.children)]])}`;
}

// Swap the body only when it changed, keeping keyboard focus on the same control.
function setBody(html) {
  if (html === lastHtml) return;
  lastHtml = html;
  const body = document.getElementById("proc-body"), f = document.activeElement;
  const fSel = body.contains(f) ? (f.dataset.key ? `[data-key="${CSS.escape(f.dataset.key)}"]` : f.matches("[data-copy]") ? "[data-copy]" : null) : null;
  body.innerHTML = html;
  if (fSel) body.querySelector(fSel)?.focus({ preventScroll: true });
}

export async function openProcess(pid, start_ts, from, again = false) {
  const d = dlg();
  if (!d.open) {
    opener = from ?? document.activeElement;
    openerKey = opener?.dataset?.key ?? null;
    openerBox = opener?.closest?.("[id]") ?? null; // rebuilt openers are looked up only in here
    d.showModal();
  }
  const my = ++token;
  if (!again) {
    cur = { pid, start_ts };
    copied = "";
    const row = snapshot()?.processes.find((r) => r.pid === pid && r.start_ts === start_ts);
    document.getElementById("proc-title").textContent = row ? `${row.name} (PID ${pid})` : `PID ${pid}`;
    setBody(`<p class="empty">Loading…</p>`);
  }
  let p;
  try {
    p = await src.process(pid, start_ts);
  } catch (e) {
    if (my !== token) return;
    if (e.status === 404) { cur = null; setBody(`<p class="empty">This process has exited.</p>`); } // stop refreshing
    else setBody(`<p class="empty">Could not load, retrying…</p>`);
    return;
  }
  if (my !== token || !d.open) return;
  document.getElementById("proc-title").textContent = `${p.name} (PID ${p.pid})`;
  setBody(render(p));
}

export function refreshProcess() {
  if (cur && dlg().open) openProcess(cur.pid, cur.start_ts, null, true);
}

function openFrom(el) {
  const [pid, start_ts] = el.dataset.key.split(":").map(Number);
  openProcess(pid, start_ts, el);
}

// source: LiveSource/DemoSource; getSnapshot: latest /api/live body (row names, current CPU).
export function bindProcess(source, getSnapshot) {
  src = source;
  snapshot = getSnapshot;
  const d = dlg();
  document.addEventListener("click", (e) => {
    const el = e.target.closest?.(`${ROWS}, #insights [data-key], #proc-body [data-key]`);
    if (el) openFrom(el);
  });
  document.addEventListener("keydown", (e) => {
    const el = e.target.closest?.(ROWS);
    if (!el || e.target !== el || (e.key !== "Enter" && e.key !== " ")) return;
    e.preventDefault();
    openFrom(el);
  });
  d.addEventListener("click", async (e) => {
    if (e.target === d) return d.close(); // backdrop
    if (!e.target.closest("[data-copy]")) return;
    const msg = d.querySelector("[data-copied]");
    try {
      await navigator.clipboard.writeText(d.querySelector(".cmd-full").textContent);
      copied = "Copied";
    } catch {
      getSelection().selectAllChildren(d.querySelector(".cmd-full")); // let the user copy by hand
      copied = "Copy failed. Text selected, press Ctrl+C or ⌘C.";
    }
    msg.textContent = copied;
  });
  d.addEventListener("close", () => {
    token++;
    cur = null;
    lastHtml = "";
    // the opener may have been rebuilt by a poll while the dialog was open
    const back = opener?.isConnected ? opener
      : openerKey && openerBox?.querySelector(`[data-key="${CSS.escape(openerKey)}"]`);
    (back || document.getElementById("live-search")).focus(); // opener gone: back to search
    opener = openerKey = openerBox = null;
  });
}
