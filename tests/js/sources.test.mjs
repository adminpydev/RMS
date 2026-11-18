import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DemoSource, LiveSource, loadSource, outdated } from "../../rms/web/sources.js";
import { insights } from "../../rms/web/insights.js";

const sample = JSON.parse(readFileSync(new URL("../../site/sample.json", import.meta.url)));
const NOW = 1800000000; // fixed clock anchor: deterministic and TZ-independent
const S = sample.snapshots;

// elapsed real seconds -> DemoSource with an injected clock
const demo = () => {
  const c = { t: NOW };
  return [new DemoSource(sample, { clock: () => c.t }), (s) => { c.t = NOW + s; }];
};
const live = async (src, set, s) => { set(s); return src.live(); };
const pids = (l) => l.processes.map((p) => p.pid).join(",");

test("meta: dataset meta with features now", async () => {
  const [src] = demo();
  const m = await src.meta();
  assert.equal(m.mode, "demo");
  assert.deepEqual(m.features, ["now"]);
  assert.equal(m.total_ram, sample.meta.total_ram);
});

test("live cycles every 6 s from snapshot 2 and wraps over 5", async () => {
  const [src, set] = demo();
  const order = [];
  for (let k = 0; k < 7; k++) order.push(pids(await live(src, set, k * 6 + 3)));
  const ref = S.map((s) => s.processes.map((p) => p.pid).join(","));
  assert.deepEqual(order, [1, 2, 3, 4, 0, 1, 2].map((i) => ref[i]));
  assert.equal(pids(await live(src, set, 5.9)), ref[1]);
  assert.equal(pids(await live(src, set, 6)), ref[2]);
  assert.equal(pids(await live(src, set, -1)), ref[0]); // clock skew stays in range
});

test("live: simulated, no details, same window identical, next cycle differs", async () => {
  const [src, set] = demo();
  const a = await live(src, set, 0), b = await live(src, set, 5);
  assert.equal(a.simulated, true);
  assert.equal("details" in a, false);
  assert.deepEqual(a, b);
  const c = await live(src, set, 30); // same snapshot (index 1), next cycle
  assert.equal(pids(c), pids(a));
  assert.notDeepEqual(c.processes.map((p) => p.rss), a.processes.map((p) => p.rss));
});

test("jitter is within ±2%, shares recomputed, apps re-summed, dataset untouched", async () => {
  const before = JSON.stringify(sample);
  const [src, set] = demo();
  for (let k = 0; k < 15; k++) {
    const l = await live(src, set, k * 6), base = S[(1 + k) % 5], total = l.system.total;
    base.processes.forEach((b, i) => {
      const p = l.processes[i];
      assert.equal(p.pid, b.pid);
      if (b.rss == null) { assert.equal(p.rss, null); assert.equal(p.share, null); }
      else {
        assert.ok(Math.abs(p.rss / b.rss - 1) <= 0.02 + 1e-9, `rss ${p.pid}`);
        assert.equal(p.share, Math.round((p.rss / total) * 1e4) / 1e4);
      }
      if (b.cpu == null) assert.equal(p.cpu, null);
      else { assert.ok(Number.isInteger(p.cpu)); assert.ok(Math.abs(p.cpu - b.cpu) <= Math.ceil(b.cpu * 0.02) + 0.5); }
    });
    assert.equal(l.apps.length, base.apps.length);
    for (const a of l.apps) {
      const ps = l.processes.filter((p) => p.app === a.app);
      const rss = ps.reduce((s, p) => s + (p.rss ?? 0), 0);
      assert.equal(a.rss, rss);
      assert.equal(a.share, Math.round((rss / total) * 1e4) / 1e4);
      assert.equal(a.procs, base.apps.find((x) => x.app === a.app).procs);
    }
  }
  assert.equal(JSON.stringify(sample), before);
});

test("process(): current snapshot detail with jittered rss, else 404", async () => {
  const [src, set] = demo();
  set(0); // snapshot 2
  const l = await src.live(), p = l.processes.find((x) => x.pid === 1288);
  const d = await src.process(1288, p.start_ts);
  assert.equal(d.pid, 1288);
  assert.equal(d.rss, p.rss);
  assert.equal(d.name, "qemu-system-aarch64");
  await assert.rejects(src.process(1288, p.start_ts + 1), (e) => e.status === 404);
  await assert.rejects(src.process(-5, 0), (e) => e.status === 404);
  // a pid only in another snapshot is 404 now
  const other = S[0].processes.find((x) => !S[1].details[`${x.pid}:${x.start_ts}`]);
  if (other) await assert.rejects(src.process(other.pid, other.start_ts), (e) => e.status === 404);
});

test("every insight kind fires on its snapshot across 3 cycles after jitter", async () => {
  const [src, set] = demo();
  const want = { 0: [], 1: ["bigproc"], 2: ["bigapp", "manyprocs"], 3: ["pressure", "swap"], 4: ["zombie"] };
  const seen = new Set();
  for (let k = 0; k < 15; k++) {
    const idx = (1 + k) % 5, ins = insights({ live: await live(src, set, k * 6) });
    const kinds = ins.map((i) => i.kind);
    for (const w of want[idx]) assert.ok(kinds.includes(w), `snapshot ${idx + 1} cycle ${k}: ${kinds}`);
    if (idx === 0) assert.deepEqual(kinds, []);
    kinds.forEach((x) => seen.add(x));
  }
  assert.deepEqual([...seen].sort(), ["bigapp", "bigproc", "manyprocs", "pressure", "swap", "zombie"]);
});

test("LiveSource calls the v0.4 endpoints; errors carry status", async () => {
  const calls = [], orig = globalThis.fetch;
  globalThis.fetch = async (u) => {
    calls.push(u);
    const bad = u.includes("pid=9");
    return { ok: !bad, status: bad ? 404 : 200, json: async () => (bad ? { error: "not found" } : { u }) };
  };
  try {
    const s = new LiveSource();
    await s.meta(); await s.live(); await s.process(42, 1700000000);
    assert.deepEqual(calls, ["/api/meta", "/api/live", "/api/process?pid=42&start_ts=1700000000"]);
    await assert.rejects(s.process(9, 1), (e) => e.status === 404 && /not found/.test(e.message));
  } finally { globalThis.fetch = orig; }
});

test("loadSource: localhost takes any 200 /api/meta as LiveSource; elsewhere demo", async () => {
  const orig = globalThis.fetch;
  globalThis.fetch = async (u) => (u === "/api/meta"
    ? { ok: true, json: async () => ({ version: "0.2.0" }) }
    : { ok: true, json: async () => sample });
  try {
    globalThis.location = { hostname: "localhost" };
    assert.ok((await loadSource()) instanceof LiveSource);
    globalThis.location = { hostname: "127.0.0.1" };
    assert.ok((await loadSource()) instanceof LiveSource);
    globalThis.location = { hostname: "example.github.io" };
    assert.ok((await loadSource()) instanceof DemoSource);
  } finally { globalThis.fetch = orig; delete globalThis.location; }
});

test("outdated(meta): version < 0.4.0 or no features now", () => {
  const f = ["now"];
  for (const m of [undefined, null, {}, { version: "0.4.0" }, { version: "0.4.0", features: ["live"] },
    { version: "0.3.9", features: f }, { version: "0.2", features: f }, { version: "x.y", features: f },
    { features: f }]) assert.equal(outdated(m), true, JSON.stringify(m));
  for (const v of ["0.4.0", "0.4", "0.4.1", "0.10.0", "1.0.0", "0.4.0rc1"])
    assert.equal(outdated({ version: v, features: ["live", "now"] }), false, v);
});
