import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { DemoSource } from "../../rms/web/sources.js";
import { MB, stackLayers, topApps, detectLeaks, detectSpikes, explainSpike, heatmap }
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
    const r = await src.apps(s.peakTs - 600, s.peakTs + sys.step, { step: meta.interval, top: "all" });
    const ex = explainSpike(r.apps, s.peakTs + sys.step);
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
