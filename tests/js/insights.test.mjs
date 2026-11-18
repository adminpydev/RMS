import test from "node:test";
import assert from "node:assert/strict";
import { insights } from "../../rms/web/insights.js";

const MB = 2 ** 20, GB = 2 ** 30, T = 16 * GB;
const sys = (o = {}) => ({ total: T, used: 8 * GB, available: 8 * GB, swap_total: 2 * GB, swap_used: 0,
                           pressure: 1, ...o });
const proc = (o) => ({ pid: 100, ppid: 1, start_ts: 1700000000, name: "x", app: "X", user: "u",
                       cmdline: "x", rss: 100 * MB, share: 0.0061, cpu: 0, threads: 1, status: "running", ...o });
const app = (o) => ({ app: "X", rss: 100 * MB, share: 0.0061, procs: 1, ...o });
const live = (processes = [proc({})], apps = [app({})], s = {}) =>
  ({ ts: 1700000000, simulated: false, system: sys(s), apps, processes });
const kinds = (r) => r.map((i) => i.kind);

test("calm -> []; null system or no processes -> []", () => {
  assert.deepEqual(insights({ live: live() }), []);
  assert.deepEqual(insights({ live: null }), []);
  assert.deepEqual(insights({ live: { ...live(), system: null } }), []);
  assert.deepEqual(insights({ live: live([], [], { pressure: 4 }) }), []);
});

test("pressure: level >= 2 or used/total > 0.90, with top 3 apps", () => {
  const apps = [app({ app: "Slack", rss: 1.2 * GB }), app({ app: "Chrome", rss: 3.1 * GB }),
                app({ app: "Docker", rss: 2.4 * GB }), app({ app: "Tiny", rss: 5 * MB })];
  const r = insights({ live: live([proc({})], apps, { pressure: 2 }) });
  assert.deepEqual(r, [{ kind: "pressure", severity: "warn",
    text: "Memory pressure is high. Top apps: Chrome 3.1 GB, Docker 2.4 GB, Slack 1.2 GB." }]);
  assert.deepEqual(kinds(insights({ live: live([proc({})], apps, { used: 0.91 * T }) })), ["pressure"]);
  assert.deepEqual(insights({ live: live([proc({})], apps, { used: 0.9 * T }) }), []);
  assert.equal(insights({ live: live([proc({})], [], { pressure: 3 }) })[0].text, "Memory pressure is high.");
});

test("swap > 1 GiB", () => {
  assert.deepEqual(insights({ live: live([proc({})], [app({})], { swap_used: 2.3 * GB }) }),
    [{ kind: "swap", severity: "warn", text: "Swap in use: 2.3 GB. RAM is likely short." }]);
  assert.deepEqual(insights({ live: live([proc({})], [app({})], { swap_used: GB }) }), []);
  assert.equal(insights({ live: live([proc({})], [app({})], { swap_used: GB + 1 }) })[0].text,
    "Swap in use: 1.0 GB. RAM is likely short.");
});

test("bigproc: share > 0.20, share desc, carries pid/start_ts/app", () => {
  const a = proc({ pid: 1288, name: "qemu-system-aarch64", app: "Docker", rss: 3.7 * GB, share: 0.2313 });
  const b = proc({ pid: 7, name: "big", app: "B", rss: 3.9 * GB, share: 0.2438 });
  const r = insights({ live: live([a, proc({ share: 0.2 }), b], [app({})]) });
  assert.deepEqual(kinds(r), ["bigproc", "bigproc"]);
  assert.equal(r[1].text, "qemu-system-aarch64 (PID 1288) is using 23.1% of RAM (3.7 GB).");
  assert.deepEqual([r[1].severity, r[1].pid, r[1].start_ts, r[1].app], ["warn", 1288, 1700000000, "Docker"]);
  assert.equal(r[0].pid, 7);
});

test("bigapp: share > 0.30, skipped when the app already has bigproc", () => {
  const chrome = app({ app: "Google Chrome", rss: 5.6 * GB, share: 0.348, procs: 27 });
  const r = insights({ live: live([proc({})], [chrome, app({ share: 0.3 })]) });
  assert.deepEqual(r, [{ kind: "bigapp", severity: "info", app: "Google Chrome",
    text: "Google Chrome is using 34.8% of RAM across 27 processes." }]);
  const big = proc({ app: "Google Chrome", share: 0.25, rss: 4 * GB });
  assert.deepEqual(kinds(insights({ live: live([big], [chrome]) })), ["bigproc"]);
});

test("manyprocs: procs > 30", () => {
  const r = insights({ live: live([proc({})], [app({ app: "Google Chrome", procs: 42 }), app({ procs: 30 })]) });
  assert.deepEqual(r, [{ kind: "manyprocs", severity: "info", app: "Google Chrome",
    text: "Google Chrome is running 42 processes." }]);
});

test("zombie: count, singular and plural", () => {
  const z = (pid) => proc({ pid, status: "zombie", rss: null, share: null, cpu: null });
  assert.deepEqual(insights({ live: live([proc({}), z(1), z(2), z(3)]) }),
    [{ kind: "zombie", severity: "info", text: "3 zombie processes (finished but not cleaned up by their parent)." }]);
  assert.equal(insights({ live: live([z(1)]) })[0].text,
    "1 zombie process (finished but not cleaned up by their parent).");
});

test("order and cap 4", () => {
  const procs = [proc({ pid: 1, status: "zombie" }), proc({ pid: 2, app: "P", share: 0.21, rss: 3.4 * GB }),
                 proc({ pid: 3, app: "Q", share: 0.22, rss: 3.5 * GB })];
  const apps = [app({ app: "M", procs: 40 }), app({ app: "A", share: 0.31, rss: 5 * GB })];
  const r = insights({ live: live(procs, apps, { pressure: 2, swap_used: 2 * GB }) });
  assert.deepEqual(kinds(r), ["pressure", "swap", "bigproc", "bigproc"]);
  assert.deepEqual(kinds(insights({ live: live(procs, apps) })), ["bigproc", "bigproc", "bigapp", "manyprocs"]);
  assert.deepEqual(kinds(insights({ live: live([procs[0]], apps) })), ["bigapp", "manyprocs", "zombie"]);
});

test("null rss/cpu/share are safe", () => {
  const n = proc({ rss: null, share: null, cpu: null });
  assert.deepEqual(insights({ live: live([n, proc({ cpu: null })], [app({ rss: 0, share: 0 })]) }), []);
});

test("long names are clipped at a space", () => {
  const name = "a ".repeat(30) + "end";
  const r = insights({ live: live([proc({ name, share: 0.25, rss: 4 * GB })], [app({})]) });
  assert.ok(r[0].text.startsWith("a a"));
  assert.ok(r[0].text.includes("… (PID 100)"));
});
