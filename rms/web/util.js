// Shared formatting helpers. No imports, so every module can use them.
const MiB = 2 ** 20, GiB = 2 ** 30;
const DASH = "—";

export const esc = (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);
export const fmtBytes = (b) => (b == null ? DASH : Math.abs(b) >= GiB ? `${(b / GiB).toFixed(1)} GiB` : `${(b / MiB).toFixed(1)} MiB`);
export const fmtPct = (s) => (s == null ? DASH : `${(s * 100).toFixed(1)}%`); // share is 0..1
export const fmtCpu = (c) => (c == null ? DASH : `${(c / 10).toFixed(1)}%`); // cpu is percent x10
export const fmtNum = (n) => (n == null ? DASH : Number(n).toLocaleString());
export const fmtTime = (ts) => new Date(ts * 1000).toLocaleString([], { weekday: "short", month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
export const procKey = (p) => `${p.pid}:${p.start_ts}`;

// CPU seconds as "2 h 5 min", "3 min 20 s" or "4.2 s".
export function fmtSecs(s) {
  if (s == null) return DASH;
  if (s < 60) return `${s.toFixed(1)} s`;
  const m = Math.floor(s / 60);
  return m < 60 ? `${m} min ${Math.floor(s % 60)} s` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

const NORMAL = ["running", "sleeping", "idle", "disk-sleep"];
// Status badge only for unusual states (zombie, stopped, ...); plain muted text otherwise.
export const statusCell = (st) => (st == null ? DASH : NORMAL.includes(st)
  ? `<span class="muted">${esc(st)}</span>` : `<span class="badge st">${esc(st)}</span>`);
