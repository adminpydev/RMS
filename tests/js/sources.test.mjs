import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DemoSource, LiveSource, loadSource, outdated } from "../../rms/web/sources.js";
import { insights } from "../../rms/web/insights.js";

const sample = JSON.parse(readFileSync(new URL("../../site/sample.json", import.meta.url)));
const NOW = 1800000000; // fixed real-time anchor so tests are deterministic (and TZ-independent)
const T0 = sample.meta.t0;
const MB = 2 ** 20;
const PID = { notion: 3121, notionApp: 3105, slack: 3502, python: 14207 };

// elapsed real seconds -> DemoSource with an injected clock
const demo = () => {
  const c = { t: NOW };
  return [new DemoSource(sample, { clock: () => c.t }), (s) => { c.t = NOW + s; }];
};
const at = async (s, src, set) => { set(s); const live = await src.live(); return [live, insights({ live })]; };
const has = (ins, re, pid) => ins.some((i) => re.test(i.text) && (pid == null || i.pid === pid));

test("meta: live-only demo", async () => {
  const [src] = demo();
  const m = await src.meta();
  assert.equal(m.mode, "demo");
  assert.deepEqual(m.features, ["live"]);
  assert.equal(m.interval, 10);
  assert.equal(m.total_ram, sample.meta.total_ram);
});

test("first live() starts at T0 (anchored to now) with steady + fast grower insights", async () => {
  const [src, set] = demo();
  const [live, ins] = await at(0, src, set);
  assert.equal(live.ts, NOW);
  assert.equal(live.simulated, true);
  assert.equal(live.resolution, 2);
  for (const k of ["total", "used", "available", "swap_used", "swap_used_10m_ago", "pressure"]) {
    assert.ok(k in live.system, k);
  }
  const p = live.processes.find((x) => x.pid === PID.notion);
  for (const k of ["pid", "ppid", "start_ts", "name", "app", "user", "cmdline", "rss", "cpu", "threads",
                   "growth_5m", "growth_30m", "trend", "spark"]) assert.ok(k in p, k);
  assert.ok(p.growth_30m > 200 * MB && p.trend.r2 > 0.8, JSON.stringify(p.trend));
  assert.ok(p.spark.length > 1 && p.spark.length <= 30 && p.spark.at(-1) === p.rss);
  assert.ok(has(ins, /^Notion Helper \(Renderer\) \(PID 3121\) grew steadily: \+\d+ MB in 30 min\.$/, PID.notion), JSON.stringify(ins));
  assert.ok(has(ins, /grew \d+ MB in the last 5 min\.$/, PID.slack), JSON.stringify(ins));
  assert.ok(!live.processes.some((x) => x.pid === PID.python));
});

test("newcomer appears at elapsed 1 s; young process has no growth_30m/trend", async () => {
  const [src, set] = demo();
  const [live, ins] = await at(1, src, set);
  assert.equal(live.ts, NOW + 60);
  const py = live.processes.find((x) => x.pid === PID.python);
  assert.equal(py.growth_30m, null);
  assert.equal(py.trend, null);
  assert.ok(ins.some((i) => i.severity === "info" && i.pid === PID.python && /^New process `/.test(i.text)),
            JSON.stringify(ins));
});

test("swap insight at T0+30 min (fires T0+26.3..33.7), pressure at T0+45 min", async () => {
  const [src, set] = demo();
  let [, ins] = await at(30, src, set);
  assert.ok(has(ins, /^Swap grew .* in 10 min\./), JSON.stringify(ins));
  [, ins] = await at(45, src, set);
  assert.ok(has(ins, /^Memory pressure is high\./), JSON.stringify(ins));
  assert.equal(ins[0].text.startsWith("Memory pressure"), true);
});

test("replay wraps to T0 and insights refire", async () => {
  const [src, set] = demo();
  const P = (sample.meta.last_ts - T0 + sample.meta.interval) / 60; // wall seconds per loop
  let [live, ins] = await at(P - 0.1, src, set);
  assert.equal(live.ts, NOW + sample.meta.last_ts - T0);
  assert.ok(!has(ins, /steadily/));
  [live, ins] = await at(P, src, set);
  assert.equal(live.ts, NOW);
  assert.ok(has(ins, /steadily/, PID.notion) && has(ins, /last 5 min/, PID.slack), JSON.stringify(ins));
  [live] = await at(-1, src, set); // clock skew: still inside T0..last_ts
  assert.ok(live.ts >= NOW && live.ts <= NOW + sample.meta.last_ts - T0, `${live.ts - NOW}`);
});

test("process(): server shape, parent/children, 30-min series, not found", async () => {
  const [src, set] = demo();
  set(3);
  const live = await src.live();
  const row = live.processes.find((x) => x.pid === PID.notion);
  const d = await src.process(row.pid, row.start_ts);
  for (const k of ["pid", "ppid", "start_ts", "name", "app", "user", "cmdline"]) assert.equal(d[k], row[k], k);
  assert.equal(d.alive, true);
  assert.deepEqual(d.parent, { pid: PID.notionApp, name: "Notion" });
  assert.deepEqual(d.children, []);
  assert.equal(d.series.length, 181);
  assert.equal(d.series.at(-1).ts, live.ts);
  assert.equal(d.series[0].ts, live.ts - 1800);
  assert.ok(d.series.every((s) => s.rss > 0 && "cpu" in s));
  const app = live.processes.find((x) => x.pid === PID.notionApp);
  const a = await src.process(app.pid, app.start_ts);
  assert.deepEqual(a.children.map((c) => [c.pid, c.name]), [[PID.notion, "Notion Helper (Renderer)"]]);
  // python3 started 2 sim-min ago: series only since start
  const py = live.processes.find((x) => x.pid === PID.python);
  assert.equal((await src.process(py.pid, py.start_ts)).series.length, 13);
  await assert.rejects(src.process(PID.notion, row.start_ts + 1), (e) => e.status === 404);
  set(0); // python3 not started yet
  await assert.rejects(src.process(py.pid, py.start_ts), /not found/);
});

test("LiveSource calls the v0.3 endpoints", async () => {
  const calls = [], orig = globalThis.fetch;
  globalThis.fetch = async (u) => {
    calls.push(u);
    const nf = u.startsWith("/api/process") && u.includes("pid=9");
    return { ok: !nf, status: nf ? 404 : 200, json: async () => (nf ? { error: "not found" } : { u }) };
  };
  try {
    const s = new LiveSource();
    await s.meta();
    await s.live();
    await s.live({ fast: true });
    await s.process(12, 345);
    await assert.rejects(s.process(9, 1), (e) => e.status === 404 && /not found/.test(e.message));
    assert.deepEqual(calls,
                     ["/api/meta", "/api/live", "/api/live?fast=1", "/api/process?pid=12&start_ts=345",
                      "/api/process?pid=9&start_ts=1"]);
  } finally { globalThis.fetch = orig; }
});

test("loadSource: localhost takes any 200 /api/meta as LiveSource; elsewhere only mode live", async () => {
  const orig = globalThis.fetch;
  globalThis.fetch = async (u) => (u === "/api/meta"
    ? { ok: true, status: 200, json: async () => ({ mode: "local", version: "0.2.1" }) }
    : { ok: true, status: 200, json: async () => sample });
  try {
    globalThis.location = { hostname: "localhost" };
    assert.ok((await loadSource()) instanceof LiveSource);
    globalThis.location = { hostname: "127.0.0.1" };
    assert.ok((await loadSource()) instanceof LiveSource);
    globalThis.location = { hostname: "example.github.io" };
    assert.ok((await loadSource()) instanceof DemoSource);
  } finally { globalThis.fetch = orig; delete globalThis.location; }
});

test("outdated(): missing or < 0.3.0", () => {
  for (const v of [undefined, null, "", "0.2.9", "0.2", "x.y", "0.1.0"]) assert.equal(outdated(v), true, String(v));
  for (const v of ["0.3.0", "0.3.1", "0.10.0", "1.0.0", "0.3"]) assert.equal(outdated(v), false, v);
});
