import test from "node:test";
import assert from "node:assert/strict";
import { cmpBy, matches } from "../../rms/web/live.js";
import { filterApps } from "../../rms/web/apps.js";
import { statusCell } from "../../rms/web/util.js";

const rows = [{ pid: 1, rss: 30 }, { pid: 2, rss: null }, { pid: 3, rss: 10 }, { pid: 4, rss: 20 }];
const order = (dir) => [...rows].sort(cmpBy({ key: "rss", dir })).map((r) => r.pid);

test("sort puts nulls last in both directions", () => {
  assert.deepEqual(order(-1), [1, 4, 3, 2]);
  assert.deepEqual(order(1), [3, 4, 1, 2]);
});

test("sort compares strings with localeCompare", () => {
  const r = [{ name: "b" }, { name: "a" }, { name: null }].sort(cmpBy({ key: "name", dir: 1 }));
  assert.deepEqual(r.map((x) => x.name), ["a", "b", null]);
});

const p = { pid: 4321, name: "python3", cmdline: "python3 serve.py --port 9", app: "Visual Studio Code", user: "alice" };
test("search matches name, cmdline, app, user and PID", () => {
  for (const q of ["python", "serve.py", "studio", "alice", "432"]) assert.ok(matches(p, q), q);
  assert.ok(matches(p, ""));
  assert.ok(!matches(p, "nomatch"));
  assert.ok(!matches({ pid: 1, name: null, cmdline: null, app: null, user: null }, "x"));
});

test("apps filter by app name or by a matching process", () => {
  const apps = [{ app: "Visual Studio Code" }, { app: "Slack" }, { app: "python3" }];
  assert.deepEqual(filterApps(apps, [p], "alice").map((a) => a.app), ["Visual Studio Code"]);
  assert.deepEqual(filterApps(apps, [p], "slack").map((a) => a.app), ["Slack"]);
  assert.equal(filterApps(apps, [p], "").length, 3);
});

test("status badge only for unusual states, escaped", () => {
  for (const s of ["running", "sleeping", "idle", "disk-sleep"]) assert.ok(!statusCell(s).includes("badge"), s);
  for (const s of ["zombie", "stopped"]) assert.ok(statusCell(s).includes(`class="badge st">${s}<`), s);
  assert.equal(statusCell(null), "—");
  assert.ok(statusCell("<x>").includes("&#60;x&#62;"));
});
