import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DemoSource } from "../../rms/web/sources.js";
import { MB, stackLayers, topApps, detectLeaks, detectSpikes, explainSpike, spikeWindow, heatmap }
  from "../../rms/web/analytics.js";

const sample = JSON.parse(readFileSync(new URL("../../site/sample.json", import.meta.url)));
const NOW = 1800000000;
const src = new DemoSource(sample, NOW);
const meta = await src.meta();
const FROM = meta.first_ts, TO = meta.last_ts + 1;
const sys = await src.system(FROM, TO);
const apps = await src.apps(FROM, TO, { top: "all" });

test("detectLeaks finds only Notion", () => {
  const leaks = detectLeaks(apps.apps, apps.step);
  assert.deepEqual(leaks.map((l) => l.name), ["Notion"]);
  const s = leaks[0].slopePerHour;
  assert.ok(s > 120 * MB && s < 180 * MB, `slope ${s / MB} MB/h`);
});

const spikes = detectSpikes(sys.points, meta.total_ram, sys.step);
const planted = [meta.first_ts + 14 * 3600, meta.first_ts + 2 * 86400 + 11 * 3600];

test("detectSpikes finds the two planted spikes", () => {
  assert.equal(spikes.length, 2, JSON.stringify(spikes));
  spikes.forEach((s, i) => assert.ok(Math.abs(s.peakTs - planted[i]) <= 600,
    `spike ${i} peak off by ${s.peakTs - planted[i]} s`));
});

test("explainSpike names planted causes", async () => {
  const want = ["Docker", "Google Chrome"];
  for (const [i, s] of spikes.entries()) {
    const { from, to } = spikeWindow(s);
    const r = await src.apps(from, to, { step: meta.interval, top: "all" });
    const ex = explainSpike(r.apps, s);
    assert.equal(ex[0].name, want[i], JSON.stringify(ex.slice(0, 3)));
    assert.ok(ex.every((e) => e.growth > 0 && e.name !== "Other (small apps)"));
  }
});

test("detectSpikes ignores gaps", () => {
  const G = 16 * 2 ** 30;
  const p = (ts, v) => ({ ts, used: v, used_max: v, available: G - v, pressure: 1 });
  const pts = [p(0, 2 ** 30), p(300, 2 ** 30),
    { ts: 600, used: null, used_max: null, available: null, pressure: null },
    p(900, 10 * 2 ** 30), p(1200, 10 * 2 ** 30)];
  assert.deepEqual(detectSpikes(pts, G, 300), []);
  // sanity: without the gap the same jump is a spike
  pts[2] = p(600, 2 ** 30);
  assert.equal(detectSpikes(pts, G, 300).length, 1);
});

test("stackLayers sums to total", () => {
  const L = stackLayers(sys.points, apps.apps, meta.total_ram);
  let n = 0;
  L.ts.forEach((_, i) => {
    if (L.apps[i] == null) return;
    n++;
    assert.ok(L.apps[i] >= 0 && L.otherSystem[i] >= 0 && L.available[i] >= 0);
    assert.ok(Math.abs(L.apps[i] + L.otherSystem[i] + L.available[i] - meta.total_ram) <= 1);
  });
  assert.ok(n > 900);
});

test("topApps sorted by avg", () => {
  const t = topApps(apps.apps);
  assert.equal(t.length, 13);
  t.slice(1).forEach((a, i) => assert.ok(t[i].avg >= a.avg));
  assert.ok(t[0].peak >= t[0].avg && t[0].spark.length === sys.points.length);
});

test("heatmap null under 2 days", () => {
  const day = sys.points.filter((p) => p.ts < FROM + 86400);
  assert.equal(heatmap(day), null);
  const h = heatmap(sys.points);
  assert.equal(h.length, 7);
  assert.ok(h.every((r) => r.length === 24));
  const mean = (hs) => {
    const v = h.flatMap((r) => hs.map((x) => r[x])).filter((x) => x != null);
    return v.reduce((a, b) => a + b, 0) / v.length;
  };
  assert.ok(mean([13, 14, 15]) > mean([2, 3, 4]));
});

test("detectSpikes base is the min when pressure opens the span", () => {
  const p = (ts, v, pressure = 1) => ({ ts, used: v, used_max: v, available: 1000 - v, pressure });
  const sp = detectSpikes([p(0, 500, 2), p(300, 100), p(600, 900)], 1000, 300);
  assert.equal(sp.length, 1);
  assert.equal(sp[0].rise, 800);
  assert.equal(sp[0].peakTs, 600);
  assert.equal(sp[0].peakEnd, 900);
});

test("stackLayers sums to total with linux-style available", () => {
  const L = stackLayers([{ ts: 0, used: 600, available: 300 }], { A: [{ ts: 0, rss: 400 }] }, 1000);
  assert.equal(L.apps[0] + L.otherSystem[0] + L.available[0], 1000);
  assert.equal(L.available[0], 400);
});

test("detectLeaks sees a leak in the final partial stride", () => {
  // step 5 min, 47 buckets; app opens at bucket 23 and grows 300 MiB/h to the end.
  // Strided 2h windows end at bucket 42 (presence < 80%); only the final window passes.
  const s = Array.from({ length: 47 }, (_, i) => ({
    ts: i * 300, rss: i < 23 ? 0 : 500 * MB + (i - 23) * 25 * MB, rss_max: 0, procs: 1 }));
  const L = detectLeaks({ X: s }, 300);
  assert.deepEqual(L.map((l) => l.name), ["X"]);
  assert.equal(L[0].endTs, 46 * 300);
});

test("heatmap accepts exactly 2 days of buckets", () => {
  const pts = (n) => Array.from({ length: n }, (_, i) => ({ ts: FROM + i * 3600, used: 1 }));
  assert.notEqual(heatmap(pts(48)), null);
  assert.equal(heatmap(pts(47)), null);
});

test("explainSpike names planted causes at every range step", async () => {
  const want = ["Docker", "Google Chrome"];
  const ranges = [
    ["7d", TO - 7 * 86400, TO, 630],
    ["3d", FROM, TO, sys.step],
    ...planted.map((t, i) => [`24h#${i}`, t - 12 * 3600, t + 12 * 3600, null]),
  ];
  for (const [label, from, to, wantStep] of ranges) {
    const r = await src.system(from, to);
    if (wantStep) assert.equal(r.step, wantStep, label);
    const sp = detectSpikes(r.points, meta.total_ram, r.step)
      .filter((s) => planted.some((t) => Math.abs(s.peakTs - t) <= 1800));
    const idx = sp.map((s) => planted.findIndex((t) => Math.abs(s.peakTs - t) <= 1800));
    assert.equal(sp.length, label.startsWith("24h") ? 1 : 2, `${label}: planted spikes detected`);
    for (const [j, s] of sp.entries()) {
      const w = spikeWindow(s);
      const a = await src.apps(w.from, w.to, { step: meta.interval, top: "all" });
      const ex = explainSpike(a.apps, s);
      assert.equal(ex[0]?.name, want[idx[j]], `${label} step ${r.step}: ${JSON.stringify(ex.slice(0, 3))}`);
    }
  }
});

test("detectLeaks never flags Other (small apps)", async () => {
  for (const from of [FROM, TO - 7 * 86400]) {
    const a = await src.apps(from, TO, { top: "all" });
    assert.ok(detectLeaks(a.apps, a.step).every((l) => l.name !== "Other (small apps)"));
  }
});
