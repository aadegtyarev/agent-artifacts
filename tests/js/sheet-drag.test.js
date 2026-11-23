const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

const sheetDrag = vm.runInNewContext(`${fs.readFileSync(path.join(__dirname, "../../src/agent_artifacts/static/sheet-drag.js"), "utf8")}\nsheetDrag`, { performance: { now: () => 1 } });

test("drag uses rendered enlarged strip height for floor and snap", () => {
  const handlers = {};
  const sheet = {
    dataset: { pos: "m" }, style: {}, parentElement: { clientHeight: 240 },
    classList: { add() {}, remove() {} }, setPointerCapture() {},
    addEventListener(type, fn) { (handlers[type] ||= []).push(fn); },
    getBoundingClientRect() { return { height: this.style.height ? parseFloat(this.style.height) : this.dataset.pos === "m" ? 100 : 57.2 }; },
  };
  const strip = { contains: () => false, getBoundingClientRect: () => ({ height: sheet.dataset.pos === "strip" ? 57.2 : 0 }) };
  const grip = { closest: () => null };
  let snapped;
  sheetDrag({ sheet, strip, grip, desk: { matches: false }, pos: () => sheet.dataset.pos, open() {}, snap(p) { snapped = p; }, tap() {} });
  const event = (y) => ({ target: grip, button: 0, pointerId: 1, clientY: y });
  handlers.pointerdown[0](event(0));
  handlers.pointermove[0](event(100));
  assert.equal(sheet.style.height, "57.2px");
  handlers.pointerup[0](event(100));
  assert.equal(snapped, "strip");
  handlers.pointerdown[0](event(0));
  handlers.pointermove[0](event(22)); // 78px: closer to 57.2 than 100
  handlers.pointerup[0](event(22));
  assert.equal(snapped, "strip");
});
