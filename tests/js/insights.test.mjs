import test from "node:test";
import assert from "node:assert/strict";
import { insights } from "../../rms/web/insights.js";

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
  assert.equal(r[0].text, "Notion still leaking: +140 MB/h for 3 h (PID 2231).");
  assert.equal(r[0].pid, 2231);
  assert.equal(r[0].app, "Notion");
  assert.deepEqual(insights({ live: live(), leaks: [{ ...leak, endTs: NOW - 1000 }], now: NOW }), []);
});

test("big newcomer truncates long commands", () => {
  const cmd = "node server.js --port 3000 --api-key *** --verbose --some-long-flag";
  const r = insights({ live: live([proc({ pid: 9021, start_ts: NOW - 300, rss: 900 * MB, cmdline: cmd })]),
                      leaks: [], now: NOW });
  assert.equal(r.length, 1);
  assert.equal(r[0].text, "New process `node server.js --port 3000 --api-key **…` (PID 9021) using 900 MB.");
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
