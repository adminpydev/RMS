// Insights strip: pure function over /api/live (or DemoSource.live).
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
const ref = (p) => ({ pid: p.pid, start_ts: p.start_ts, app: p.app });

export function insights({ live, now }) {
  now = now ?? live?.ts ?? null; // null: skip time-based rules (newcomer)
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
  const seen = new Set(); // one insight per process: earlier rule wins (fast beats steady)
  const add = (list, key, severity, text) => {
    for (const p of list.sort((a, b) => b[key] - a[key])) {
      const k = `${p.pid}:${p.start_ts}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push({ severity, text: text(p), ...ref(p) });
    }
  };
  add(procs.filter((p) => p.growth_5m > 250 * MiB), "growth_5m", "warn",
      (p) => `${clip(p.name)} (PID ${p.pid}) grew ${size(p.growth_5m)} in the last 5 min.`);
  add(procs.filter((p) => p.growth_30m > 200 * MiB && p.trend?.r2 > 0.8), "growth_30m", "warn",
      (p) => `${clip(p.name)} (PID ${p.pid}) grew steadily: +${size(p.growth_30m)} in 30 min.`);
  add(now == null ? [] : procs.filter((p) => p.start_ts >= now - 600 && p.rss > 500 * MiB), "rss", "info",
      (p) => `New process \`${clip(p.cmdline || p.name)}\` (PID ${p.pid}) is using ${size(p.rss)}.`);
  return out.slice(0, MAX);
}
