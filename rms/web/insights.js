// Insights strip: pure function over /api/live (or DemoSource.live) plus current-range leaks.
// Returns plain text; the UI escapes it (process strings are untrusted).
const MiB = 2 ** 20, GiB = 2 ** 30;
const MAX = 4;

const size = (b) => (Math.abs(b) >= GiB ? `${(b / GiB).toFixed(1)} GB` : `${Math.round(b / MiB)} MB`);
// Cut at the last space before the limit so a token (e.g. a redacted `***`) is never split.
const clip = (v, n = 40) => {
  const s = String(v ?? "?");
  if (s.length <= n) return s;
  const sp = s.lastIndexOf(" ", n - 1);
  return `${sp > 0 ? s.slice(0, sp) : s.slice(0, n - 1)}…`;
};
const ref = (p) => ({ pid: p.pid, start_ts: p.start_ts, id: p.id, app: p.app });

export function insights({ live, leaks = [], now }) {
  now = now ?? live?.ts ?? null; // null: skip time-based rules (leak, newcomer)
  const out = [];
  const sys = live?.system, procs = live?.processes ?? [];
  if (sys) {
    if (sys.pressure >= 2 || sys.used / sys.total > 0.9) {
      const byApp = {};
      for (const p of procs) { const a = p.app ?? p.name; byApp[a] = (byApp[a] ?? 0) + p.rss; }
      const top = Object.entries(byApp).sort((a, b) => b[1] - a[1]).slice(0, 3)
        .map(([a, r]) => `${clip(a)} ${size(r)}`);
      out.push({ severity: "warn",
                 text: `Memory pressure is high.${top.length ? ` Top users: ${top.join(", ")}.` : ""}` });
    }
    if (sys.swap_used_10m_ago != null && sys.swap_used - sys.swap_used_10m_ago > 500 * MiB) {
      out.push({ severity: "warn",
                 text: `Swap grew ${size(sys.swap_used - sys.swap_used_10m_ago)} in 10 min. The system is short on RAM.` });
    }
  }
  for (const p of procs.filter((p) => p.growth_5m > 250 * MiB).sort((a, b) => b.growth_5m - a.growth_5m)) {
    out.push({ severity: "warn", text: `${clip(p.name)} (PID ${p.pid}) grew ${size(p.growth_5m)} in the last 5 min.`,
               ...ref(p) });
  }
  for (const l of now == null ? [] : leaks.filter((l) => l.startTs <= now && l.endTs >= now - 900)) {
    const h = Math.max(1, Math.round((l.endTs - l.startTs) / 3600));
    const pid = l.pid != null ? ` (PID ${l.pid})` : "";
    out.push({ severity: "warn", text: `${clip(l.name)} is still leaking: +${size(l.slopePerHour)}/h for ${h} h${pid}.`,
               pid: l.pid, start_ts: l.start_ts, id: l.id, app: l.app ?? l.name });
  }
  for (const p of now == null ? [] : procs.filter((p) => p.start_ts >= now - 600 && p.rss > 500 * MiB)) {
    out.push({ severity: "info",
               text: `New process \`${clip(p.cmdline || p.name)}\` (PID ${p.pid}) is using ${size(p.rss)}.`, ...ref(p) });
  }
  // stable sort: warnings first, rule order within a severity
  return out.sort((a, b) => (a.severity === "warn" ? 0 : 1) - (b.severity === "warn" ? 0 : 1)).slice(0, MAX);
}
