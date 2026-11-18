// Insights strip: pure function over one /api/live snapshot (or DemoSource.live).
// Returns plain text; the UI escapes it (process strings are untrusted).
const MiB = 2 ** 20, GiB = 2 ** 30;
const MAX = 4;

const size = (b) => (Math.abs(b) >= GiB ? `${(b / GiB).toFixed(1)} GB` : `${Math.round(b / MiB)} MB`);
const pct = (s) => `${(s * 100).toFixed(1)}%`;
// Cut at the last space before the limit so a token (e.g. a redacted `***`) is never split.
const clip = (v, n = 40) => {
  const s = String(v ?? "?");
  if (s.length <= n) return s;
  const sp = s.lastIndexOf(" ", n - 1);
  return `${sp > 0 ? s.slice(0, sp) : s.slice(0, n - 1)}…`;
};

export function insights({ live }) {
  const sys = live?.system, procs = live?.processes ?? [], apps = live?.apps ?? [];
  if (!sys || !procs.length) return [];
  const out = [];
  if (sys.pressure >= 2 || sys.used / sys.total > 0.9) {
    const top = [...apps].sort((a, b) => (b.rss ?? 0) - (a.rss ?? 0)).slice(0, 3)
      .map((a) => `${clip(a.app)} ${size(a.rss ?? 0)}`);
    out.push({ kind: "pressure", severity: "warn",
               text: `Memory pressure is high.${top.length ? ` Top apps: ${top.join(", ")}.` : ""}` });
  }
  if (sys.swap_used > GiB) {
    out.push({ kind: "swap", severity: "warn", text: `Swap in use: ${size(sys.swap_used)}. RAM is likely short.` });
  }
  const bigApps = new Set(); // one insight per app/process: bigapp yields to bigproc of the same app
  for (const p of procs.filter((x) => x.share > 0.2).sort((a, b) => b.share - a.share)) {
    bigApps.add(p.app);
    out.push({ kind: "bigproc", severity: "warn", pid: p.pid, start_ts: p.start_ts, app: p.app,
               text: `${clip(p.name)} (PID ${p.pid}) is using ${pct(p.share)} of RAM (${size(p.rss)}).` });
  }
  for (const a of apps.filter((x) => x.share > 0.3 && !bigApps.has(x.app)).sort((a, b) => b.share - a.share)) {
    out.push({ kind: "bigapp", severity: "info", app: a.app,
               text: `${clip(a.app)} is using ${pct(a.share)} of RAM across ${a.procs} processes.` });
  }
  for (const a of apps.filter((x) => x.procs > 30).sort((a, b) => b.procs - a.procs)) {
    out.push({ kind: "manyprocs", severity: "info", app: a.app, text: `${clip(a.app)} is running ${a.procs} processes.` });
  }
  const z = procs.filter((p) => p.status === "zombie").length;
  if (z) {
    out.push({ kind: "zombie", severity: "info",
               text: `${z} zombie process${z > 1 ? "es" : ""} (finished but not cleaned up by their parent).` });
  }
  return out.slice(0, MAX);
}
