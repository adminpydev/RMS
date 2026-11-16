import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { bucketize, DemoSource } from "../../rms/web/sources.js";

const sample = JSON.parse(readFileSync(new URL("../../site/sample.json", import.meta.url)));
const NOW = 1800000000; // fixed anchor so tests are deterministic

const tiny = {
  meta: { interval: 10, total_ram: 1000, first_ts: 0, last_ts: 10, relative: false },
  system: { ts: [0, 10], used: [500, 500], available: [500, 500], cached: [null, null],
            compressed: [0, 0], swap_used: [0, 0], pressure: [1, 1] },
  apps: { A: { rss: [100, null], procs: [2, null] } },
};

test("bucketize matches python semantics", () => {
  const { system, apps } = bucketize(tiny, 0, 20, 20);
  assert.equal(apps.apps.A[0].rss, 50);
  assert.equal(apps.apps.A[0].rss_max, 100);
  assert.equal(apps.apps.A[0].procs, 1);
  assert.equal(system.points[0].used, 500);
  // gap bucket -> all null; `to` exclusive
  const g = bucketize(tiny, 0, 40, 20);
  assert.equal(g.system.points.length, 2);
  assert.equal(g.system.points[1].used, null);
  assert.equal(g.apps.apps.A[1].rss, null);
});

test("bucketize top keeps Other (small apps)", () => {
  const ds = structuredClone(tiny);
  ds.apps = { a: { rss: [300, 300], procs: [1, 1] }, b: { rss: [200, 200], procs: [1, 1] },
              c: { rss: [100, 100], procs: [1, 1] },
              "Other (small apps)": { rss: [5, 5], procs: [9, 9] } };
  assert.deepEqual(Object.keys(bucketize(ds, 0, 20, 10, 2).apps.apps).sort(),
                   ["Other (small apps)", "a", "b"]);
  assert.equal(Object.keys(bucketize(ds, 0, 20, 10, "all").apps.apps).length, 4);
});

test("DemoSource ends at or before now and keeps 9-5 local", async () => {
  const src = new DemoSource(sample, NOW);
  const m = await src.meta();
  assert.equal(m.mode, "demo");
  assert.ok(m.last_ts <= NOW);
  assert.equal(new Date(m.first_ts * 1000).getHours(), 0);
  const { points } = await src.system(m.first_ts, m.last_ts + 1);
  assert.ok(points.length <= 1000);
  assert.ok(points.every((p) => p.ts <= NOW));
  const avg = (h) => {
    const v = points.filter((p) => p.used != null && new Date(p.ts * 1000).getHours() === h);
    return v.reduce((s, p) => s + p.used, 0) / v.length;
  };
  assert.ok(avg(13) > avg(3), `13h ${avg(13)} vs 3h ${avg(3)}`);
});

test("DemoSource respects top=all", async () => {
  const src = new DemoSource(sample, NOW);
  const m = await src.meta();
  const r = await src.apps(m.first_ts, m.last_ts + 1, { top: "all" });
  assert.equal(Object.keys(r.apps).length, 13);
  assert.equal(Object.keys((await src.apps(m.first_ts, m.last_ts + 1, { top: 3 })).apps).length, 4);
});

test("DemoSource step clamp mirrors server", async () => {
  const src = new DemoSource(sample, NOW);
  const m = await src.meta();
  assert.equal((await src.system(m.first_ts, m.first_ts + 3600)).step, 30); // pick_step
  assert.equal((await src.system(m.first_ts, m.first_ts + 3600, 1)).step, 30); // >= interval
  assert.equal((await src.system(m.first_ts, m.first_ts + 3600, 45)).step, 60); // multiple
  const r = await src.system(m.first_ts, m.last_ts + 1, 30); // <= 5000 points
  assert.ok(r.points.length <= 5000 && r.step % 30 === 0);
});

test("DemoSource pads sparse (i0) processes to dense", async () => {
  const ds = structuredClone(tiny);
  ds.system.ts = [0, 10, 20, 30];
  for (const c of ["used", "available", "cached", "compressed", "swap_used", "pressure"]) ds.system[c] = [1, 1, 1, 1];
  ds.meta.last_ts = 30;
  ds.processes = { 7: { pid: 7, ppid: 1, name: "n", app: "A", user: "u", cmdline: "x", start_ts: 0,
                        i0: 1, rss: [5, null, 9], cpu: [1, null, 2] } };
  const src = new DemoSource(ds, NOW);
  const p = src.data.processes[7];
  assert.deepEqual(p.rss, [null, 5, null, 9]);
  assert.deepEqual(p.cpu, [null, 1, null, 2]);
  assert.equal(p.i0, undefined);
  assert.equal(p.first_seen, 10);
  assert.equal(p.last_seen, 30);
  const d = await src.process("7", 0, 40, 10);
  assert.deepEqual(d.series.map((x) => x.rss), [0, 5, 0, 9]); // missing in a sampled bucket = 0, like the store
});
