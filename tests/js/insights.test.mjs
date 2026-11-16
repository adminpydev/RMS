import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { insights } from "../../rms/web/insights.js";
import { DemoSource } from "../../rms/web/sources.js";
import { detectLeaks, processLeaks } from "../../rms/web/analytics.js";

const MB = 2 ** 20, GB = 2 ** 30, NOW = 1800000000;
const sys = (o = {}) => ({ total: 16 * GB, used: 8 * GB, available: 8 * GB, swap_used: 0,
                           swap_used_10m_ago: 0, pressure: 1, ...o });
const proc = (o) => ({ id: "7", pid: 100, ppid: 1, start_ts: NOW - 86400, name: "x", app: "X",
                       user: "u", cmdline: "x", rss: 100 * MB, cpu: 0, threads: 1, growth_5m: 0,
                       spark: null, ...o });
const live = (procs = [proc({})], s = {}) => ({ ts: NOW, system: sys(s), processes: procs });

test("calm input -> []", () => {
  assert.deepEqual(insights({ live: live(), leaks: [], now: NOW }), []);
  assert.deepEqual(insights({ live: null, leaks: [], now: NOW }), []);
});

test("fast grower", () => {
  const r = insights({ live: live([proc({ name: "Slack", pid: 812, growth_5m: 340 * MB })]), leaks: [], now: NOW });
  assert.equal(r.length, 1);
  assert.equal(r[0].severity, "warn");
  assert.equal(r[0].text, "Slack (PID 812) grew 340 MB in the last 5 min.");
  assert.equal(r[0].pid, 812);
  assert.equal(r[0].id, "7");
  assert.equal(r[0].start_ts, NOW - 86400);
  assert.equal(insights({ live: live([proc({ growth_5m: 250 * MB })]), leaks: [], now: NOW }).length, 0);
});

test("memory pressure lists top apps", () => {
  const procs = [proc({ app: "Chrome", rss: 2 * GB }), proc({ app: "Chrome", rss: 1.1 * GB }),
                 proc({ app: "Docker", rss: 2.4 * GB }), proc({ app: "Slack", rss: 1.2 * GB }),
                 proc({ app: "Tiny", rss: MB })];
  const r = insights({ live: live(procs, { pressure: 2 }), leaks: [], now: NOW });
  assert.equal(r[0].text, "Memory pressure is high. Top users: Chrome 3.1 GB, Docker 2.4 GB, Slack 1.2 GB.");
  assert.equal(r[0].pid, undefined);
  assert.equal(insights({ live: live(procs, { used: 15 * GB }), leaks: [], now: NOW }).length, 1);
});

test("ongoing leak", () => {
  const leak = { name: "Notion", slopePerHour: 140 * MB, r2: 0.99, growth: 420 * MB,
                 startTs: NOW - 3 * 3600, endTs: NOW - 600, pid: 2231, start_ts: NOW - 86400, id: "19" };
  const r = insights({ live: live(), leaks: [leak], now: NOW });
  assert.equal(r[0].text, "Notion is still leaking: +140 MB/h for 3 h (PID 2231).");
  assert.equal(r[0].pid, 2231);
  assert.equal(r[0].app, "Notion");
  assert.deepEqual(insights({ live: live(), leaks: [{ ...leak, endTs: NOW - 1000 }], now: NOW }), []);
  // empty live buffer (ts null) and no `now`: time-based rules stay silent
  assert.deepEqual(insights({ live: { ts: null, system: null, processes: [] }, leaks: [leak] }), []);
});

test("ongoing leak from real demo leaks carries the process key", async () => {
  const sample = JSON.parse(readFileSync(new URL("../../site/sample.json", import.meta.url)));
  const src = new DemoSource(sample, NOW);
  const m = await src.meta();
  const a = await src.apps(m.first_ts, m.last_ts + 1, { top: "all" });
  const p = await src.processes(m.first_ts, m.last_ts + 1, { app: "Notion", top: "all" });
  const [app] = detectLeaks(a.apps, a.step), [pl] = processLeaks(p.processes, p.step);
  assert.equal(pl.start_ts, m.first_ts + 34080);
  const leak = { ...app, pid: pl.pid, start_ts: pl.start_ts, id: pl.id }; // enrichment done by the UI
  const r = insights({ live: live(), leaks: [leak], now: app.endTs + 60 });
  assert.match(r[0].text, /^Notion is still leaking: \+1\d\d MB\/h for \d h \(PID 3121\)\.$/);
  assert.deepEqual([r[0].pid, r[0].start_ts, r[0].id, r[0].app], [3121, pl.start_ts, pl.id, "Notion"]);
});

test("big newcomer truncates long commands", () => {
  const cmd = "node server.js --port 3000 --api-key *** --verbose --some-long-flag";
  const r = insights({ live: live([proc({ pid: 9021, start_ts: NOW - 300, rss: 900 * MB, cmdline: cmd })]),
                      leaks: [], now: NOW });
  assert.equal(r.length, 1);
  assert.equal(r[0].text, "New process `node server.js --port 3000 --api-key…` (PID 9021) is using 900 MB.");
  const short = insights({ live: live([proc({ pid: 9021, start_ts: NOW - 300, rss: 900 * MB, cmdline: "node server.js" })]),
                          leaks: [], now: NOW });
  assert.equal(short[0].text, "New process `node server.js` (PID 9021) is using 900 MB.");
  // null cmdline and name fall back instead of throwing; no-space strings are hard-cut
  const anon = insights({ live: live([proc({ start_ts: NOW, rss: 900 * MB, cmdline: null, name: null })]),
                         leaks: [], now: NOW });
  assert.match(anon[0].text, /^New process `\?`/);
  const long = insights({ live: live([proc({ start_ts: NOW, rss: 900 * MB, cmdline: "x".repeat(60) })]),
                         leaks: [], now: NOW });
  assert.ok(long[0].text.includes("x".repeat(39) + "…`"));
  assert.equal(r[0].pid, 9021);
  const old = proc({ start_ts: NOW - 700, rss: 900 * MB });
  assert.deepEqual(insights({ live: live([old]), leaks: [], now: NOW }), []);
});

test("swap growth, silent while swap_used_10m_ago is null", () => {
  const r = insights({ live: live(undefined, { swap_used: 1.2 * GB, swap_used_10m_ago: 0 }), leaks: [], now: NOW });
  assert.equal(r[0].text, "Swap grew 1.2 GB in 10 min. The system is short on RAM.");
  assert.deepEqual(insights({ live: live(undefined, { swap_used: 5 * GB, swap_used_10m_ago: null }),
                              leaks: [], now: NOW }), []);
});

test("max 4, warnings first", () => {
  const procs = [proc({ pid: 1, start_ts: NOW - 10, rss: 600 * MB }),
                 ...[2, 3, 4, 5].map((pid) => proc({ pid, growth_5m: (300 + pid) * MB }))];
  const r = insights({ live: live(procs), leaks: [], now: NOW });
  assert.equal(r.length, 4);
  assert.ok(r.every((x) => x.severity === "warn"));
  const r2 = insights({ live: live(procs.slice(0, 2)), leaks: [], now: NOW });
  assert.deepEqual(r2.map((x) => x.severity), ["warn", "info"]);
});
