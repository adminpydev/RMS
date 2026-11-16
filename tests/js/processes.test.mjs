import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DemoSource, bucketProcesses } from "../../rms/web/sources.js";
import { MB, detectSpikes, spikeWindow, processLeaks, spikeProcesses } from "../../rms/web/analytics.js";

const sample = JSON.parse(readFileSync(new URL("../../site/sample.json", import.meta.url)));
const NOW = 1800000000;
const src = new DemoSource(sample, NOW);
const meta = await src.meta();
const FROM = meta.first_ts, TO = meta.last_ts + 1;
const byPid = (procs, pid) => Object.entries(procs).find(([, p]) => p.pid === pid);

test("meta advertises features", () => {
  assert.deepEqual(meta.features, ["processes", "live"]);
});

test("processes top=all returns every process, anchored, default top 25", async () => {
  const r = await src.processes(FROM, TO, { top: "all" });
  assert.equal(Object.keys(r.processes).length, 27);
  const [, n] = byPid(r.processes, 3121);
  assert.equal(n.app, "Notion");
  assert.equal(n.start_ts, FROM + 34080);
  assert.equal(n.series.length, (await src.system(FROM, TO)).points.length);
  assert.equal(Object.keys((await src.processes(FROM, TO)).processes).length, 25);
  const d = await src.processes(FROM, TO, { app: "Docker", top: "all" });
  assert.deepEqual(Object.values(d.processes).map((p) => p.pid).sort(), [1203, 1219, 1288]);
});

test("bucketProcesses mirrors query_processes (SUM/count, missing = 0, gaps null)", () => {
  const ds = {
    system: { ts: [0, 10, 20] },
    processes: {
      p1: { pid: 1, ppid: 0, name: "a", app: "A", user: "u", cmdline: "a", start_ts: 0,
            rss: [100, null, 50], cpu: [10, null, null] },
      p2: { pid: 2, ppid: 1, name: "b", app: "B", user: "u", cmdline: "b", start_ts: 10,
            rss: [null, 300, null], cpu: [null, 5, null] },
    },
  };
  const r = bucketProcesses(ds, 0, 60, 20, { top: "all" });
  assert.deepEqual(Object.keys(r), ["p2", "p1"]); // by total rss desc
  assert.deepEqual(r.p1.series[0], { ts: 0, rss: 50, rss_max: 100, cpu: 10 });
  assert.deepEqual(r.p2.series[0], { ts: 0, rss: 150, rss_max: 300, cpu: 5 });
  assert.deepEqual(r.p2.series[1], { ts: 20, rss: 0, rss_max: 0, cpu: null });
  assert.deepEqual(r.p1.series[2], { ts: 40, rss: null, rss_max: null, cpu: null });
  assert.deepEqual(Object.keys(bucketProcesses(ds, 0, 60, 20, { top: 1 })), ["p2"]);
  assert.deepEqual(Object.keys(bucketProcesses(ds, 0, 60, 20, { app: "A", top: "all" })), ["p1"]);
});

test("process() builds the Docker tree from ppid", async () => {
  const all = (await src.processes(FROM, TO, { top: "all" })).processes;
  const [bid] = byPid(all, 1219), [did] = byPid(all, 1203), [qid] = byPid(all, 1288);
  const b = await src.process(bid, FROM, TO);
  assert.deepEqual(b.parent, { id: did, pid: 1203, name: "Docker" });
  assert.deepEqual(b.children, [{ id: qid, pid: 1288, name: "qemu-system-aarch64" }]);
  assert.equal(b.first_seen, FROM);
  assert.ok(b.series.length > 0);
  const k = await src.process("p1", FROM, TO); // kernel_task, ppid 0 not stored
  assert.deepEqual(k.parent, { id: null, pid: 0, name: null });
  await assert.rejects(src.process("nope", FROM, TO), /not found/);
});

test("processLeaks on demo 3d finds only the Notion renderer", async () => {
  const r = await src.processes(FROM, TO, { top: "all" });
  const L = processLeaks(r.processes, r.step);
  assert.deepEqual(L.map((l) => [l.pid, l.name, l.app]), [[3121, "Notion Helper (Renderer)", "Notion"]]);
  assert.ok(L[0].slopePerHour > 100 * MB && L[0].slopePerHour < 200 * MB, `${L[0].slopePerHour / MB}`);
  assert.ok(L[0].id && L[0].cmdline);
  assert.equal(L[0].start_ts, FROM + 34080);
});

const planted = [FROM + 14 * 3600, FROM + 2 * 86400 + 11 * 3600];

test("spikeProcesses names qemu then the Chrome renderer at every range step", async () => {
  const want = [1288, 2402];
  const ranges = [
    ["7d", TO - 7 * 86400, TO], ["3d", FROM, TO],
    ...planted.map((t, i) => [`24h#${i}`, t - 12 * 3600, t + 12 * 3600]),
  ];
  for (const [label, from, to] of ranges) {
    const r = await src.system(from, to);
    const sp = detectSpikes(r.points, meta.total_ram, r.step)
      .filter((s) => planted.some((t) => Math.abs(s.peakTs - t) <= 1800));
    assert.ok(sp.length >= 1, label);
    for (const s of sp) {
      const idx = planted.findIndex((t) => Math.abs(s.peakTs - t) <= 1800);
      const w = spikeWindow(s);
      const p = await src.processes(w.from, w.to, { step: meta.interval, top: "all" });
      const ex = spikeProcesses(p.processes, s);
      assert.equal(ex[0]?.pid, want[idx], `${label}: ${JSON.stringify(ex.slice(0, 3))}`);
      assert.ok(ex.every((e) => e.growth > 0 && e.id && e.app));
    }
  }
});

test("live() replays at 60x from 09:00 on the last day and wraps", async () => {
  let t = 1000;
  const s = new DemoSource(sample, NOW, () => t);
  const a = await s.live();
  assert.equal(a.simulated, true);
  assert.equal(a.resolution, 2);
  assert.equal(a.ts, FROM + 2 * 86400 + 9 * 3600);
  assert.equal(new Date(a.ts * 1000).getHours(), 9);
  t += 1;
  const b = await s.live({ fast: true });
  assert.equal(b.ts - a.ts, 60);
  assert.ok(b.processes.length > 10 && b.processes.every((p) => p.id && p.rss > 0));
  assert.ok(b.system.total === meta.total_ram && b.system.used > 0);
  const r = b.processes[0];
  for (const k of ["id", "pid", "ppid", "start_ts", "name", "app", "user", "cmdline", "rss", "cpu",
                   "threads", "growth_5m", "spark"]) assert.ok(k in r, k);
  assert.ok(r.spark.length <= 20 && r.spark.length > 1);

  // near the end: node (redacted) and python3 (fast grower) are alive
  t = 1000 + (meta.last_ts - meta.first_ts - (2 * 86400 + 9 * 3600)) / 60;
  const e = await s.live();
  assert.equal(e.ts, meta.last_ts);
  const node = e.processes.find((p) => p.pid === 11842), py = e.processes.find((p) => p.pid === 14207);
  assert.match(node.cmdline, /--api-key \*\*\*/);
  assert.ok(py.growth_5m > 250 * MB, `${py.growth_5m / MB}`);
  assert.ok(e.system.swap_used_10m_ago != null);
  t += 1; // past the end -> back to 09:00 (+ a little)
  const w = await s.live();
  assert.ok(w.ts >= a.ts && w.ts < a.ts + 120, `${w.ts - a.ts}`);
  t = 999; // clock skew: elapsed -1 s still lands inside the replay range
  const n = await s.live();
  assert.ok(n.ts >= a.ts && n.ts <= meta.last_ts, `${n.ts - a.ts}`);
});
