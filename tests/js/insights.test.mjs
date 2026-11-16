import test from "node:test";
import assert from "node:assert/strict";
import { insights } from "../../rms/web/insights.js";

const MB = 2 ** 20, GB = 2 ** 30, NOW = 1800000000;
const sys = (o = {}) => ({ total: 16 * GB, used: 8 * GB, available: 8 * GB, swap_used: 0,
                           swap_used_10m_ago: 0, pressure: 1, ...o });
const proc = (o) => ({ pid: 100, ppid: 1, start_ts: NOW - 86400, name: "x", app: "X",
                       user: "u", cmdline: "x", rss: 100 * MB, cpu: 0, threads: 1, growth_5m: 0,
                       growth_30m: 0, trend: null, spark: null, ...o });
const live = (procs = [proc({})], s = {}) => ({ ts: NOW, resolution: 10, simulated: false,
                                               system: sys(s), processes: procs });
const steady = (o) => proc({ growth_30m: 420 * MB, trend: { r2: 0.95 }, ...o });

test("calm input -> []; empty buffer -> []", () => {
  assert.deepEqual(insights({ live: live(), now: NOW }), []);
  assert.deepEqual(insights({ live: null, now: NOW }), []);
  assert.deepEqual(insights({ live: { ts: null, resolution: null, simulated: false, system: null, processes: [] } }), []);
});

test("fast grower", () => {
  const r = insights({ live: live([proc({ name: "Slack", pid: 812, growth_5m: 340 * MB })]), now: NOW });
  assert.equal(r.length, 1);
  assert.equal(r[0].severity, "warn");
  assert.equal(r[0].text, "Slack (PID 812) grew 340 MB in the last 5 min.");
  assert.deepEqual([r[0].pid, r[0].start_ts, r[0].app], [812, NOW - 86400, "X"]);
  assert.equal(insights({ live: live([proc({ growth_5m: 250 * MB })]), now: NOW }).length, 0);
});

test("steady growth", () => {
  const r = insights({ live: live([steady({ name: "Notion Helper", pid: 2231 })]), now: NOW });
  assert.equal(r.length, 1);
  assert.equal(r[0].severity, "warn");
  assert.equal(r[0].text, "Notion Helper (PID 2231) grew steadily: +420 MB in 30 min.");
  assert.deepEqual([r[0].pid, r[0].start_ts, r[0].app], [2231, NOW - 86400, "X"]);
  assert.equal(insights({ live: live([steady({ growth_30m: 1.5 * GB })]) })[0].text,
               "x (PID 100) grew steadily: +1.5 GB in 30 min.");
  assert.deepEqual(insights({ live: live([steady({ growth_30m: 200 * MB })]) }), []);
  assert.deepEqual(insights({ live: live([steady({ trend: { r2: 0.8 } })]) }), []);
});

test("steady silent when trend null", () => {
  assert.deepEqual(insights({ live: live([steady({ trend: null })]), now: NOW }), []);
  assert.deepEqual(insights({ live: live([steady({ growth_30m: null, trend: null })]), now: NOW }), []);
});

test("one insight per process: fast beats steady", () => {
  const r = insights({ live: live([steady({ pid: 5, growth_5m: 300 * MB })]), now: NOW });
  assert.equal(r.length, 1);
  assert.match(r[0].text, /in the last 5 min\.$/);
});

test("memory pressure lists top apps", () => {
  const procs = [proc({ app: "Chrome", rss: 2 * GB }), proc({ app: "Chrome", rss: 1.1 * GB }),
                 proc({ app: "Docker", rss: 2.4 * GB }), proc({ app: "Slack", rss: 1.2 * GB }),
                 proc({ app: "Tiny", rss: MB })];
  const r = insights({ live: live(procs, { pressure: 2 }), now: NOW });
  assert.equal(r[0].text, "Memory pressure is high. Top users: Chrome 3.1 GB, Docker 2.4 GB, Slack 1.2 GB.");
  assert.equal(r[0].pid, undefined);
  assert.equal(insights({ live: live(procs, { used: 15 * GB }), now: NOW }).length, 1);
});

test("big newcomer truncates long commands; skipped without a clock", () => {
  const cmd = "node server.js --port 3000 --api-key *** --verbose --some-long-flag";
  const r = insights({ live: live([proc({ pid: 9021, start_ts: NOW - 300, rss: 900 * MB, cmdline: cmd })]), now: NOW });
  assert.equal(r.length, 1);
  assert.equal(r[0].severity, "info");
  assert.equal(r[0].text, "New process `node server.js --port 3000 --api-key…` (PID 9021) is using 900 MB.");
  assert.equal(r[0].pid, 9021);
  const anon = insights({ live: live([proc({ start_ts: NOW, rss: 900 * MB, cmdline: null, name: null })]), now: NOW });
  assert.match(anon[0].text, /^New process `\?`/);
  const long = insights({ live: live([proc({ start_ts: NOW, rss: 900 * MB, cmdline: "x".repeat(60) })]), now: NOW });
  assert.ok(long[0].text.includes("x".repeat(39) + "…`"));
  assert.deepEqual(insights({ live: live([proc({ start_ts: NOW - 700, rss: 900 * MB })]), now: NOW }), []);
  // `now` defaults to live.ts; with neither, time rules are skipped
  assert.equal(insights({ live: live([proc({ start_ts: NOW - 300, rss: 900 * MB })]) }).length, 1);
  const noTs = { ...live([proc({ start_ts: NOW - 300, rss: 900 * MB })]), ts: null };
  assert.deepEqual(insights({ live: noTs }), []);
});

test("swap growth, silent while swap_used_10m_ago is null", () => {
  const r = insights({ live: live(undefined, { swap_used: 1.2 * GB, swap_used_10m_ago: 0 }), now: NOW });
  assert.equal(r[0].text, "Swap grew 1.2 GB in 10 min. The system is short on RAM.");
  assert.deepEqual(insights({ live: live(undefined, { swap_used: 5 * GB, swap_used_10m_ago: null }), now: NOW }), []);
});

test("order: pressure, swap, fast (desc), steady (desc), newcomer (rss desc); cap 4", () => {
  const procs = [proc({ pid: 1, start_ts: NOW - 10, rss: 600 * MB }), proc({ pid: 2, start_ts: NOW - 10, rss: 900 * MB }),
                 steady({ pid: 3, growth_30m: 300 * MB }), steady({ pid: 4, growth_30m: 500 * MB }),
                 proc({ pid: 5, growth_5m: 260 * MB }), proc({ pid: 6, growth_5m: 400 * MB })];
  const all = insights({ live: live(procs), now: NOW });
  assert.equal(all.length, 4);
  assert.deepEqual(all.map((i) => i.pid), [6, 5, 4, 3]);
  const few = insights({ live: live([procs[0], procs[1], procs[2]], { pressure: 2, swap_used: GB }), now: NOW });
  assert.deepEqual(few.map((i) => i.pid ?? i.text.slice(0, 4)), ["Memo", "Swap", 3, 2]);
});
