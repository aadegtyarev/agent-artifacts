// The floating ≡, static/float-tip.js: its double tap and the full-screen
// tip's count. Run: node --test tests/js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { KEY, MOST, isDoubleTap, read, due, counted, hidden, place } = require(path.join(__dirname, "../../src/agent_artifacts/static/float-tip.js"));

test("a second press soon after a tap and close to it is a double tap", () => {
  const first = { at: 1000, x: 100, y: 200 };
  assert.equal(isDoubleTap(first, { at: 1250, x: 110, y: 210 }), true);
  assert.equal(isDoubleTap(first, { at: 1300, x: 100, y: 220 }), true); // the limits count
  assert.equal(isDoubleTap(first, { at: 1301, x: 100, y: 200 }), false); // too late
  assert.equal(isDoubleTap(first, { at: 1100, x: 125, y: 200 }), false); // too far
  assert.equal(isDoubleTap(null, { at: 1100, x: 100, y: 200 }), false); // no first tap
  assert.equal(isDoubleTap(first, { at: 900, x: 100, y: 200 }), false); // a stale clock
});

test("the tip shows three times, then never; Hide stops it at once", () => {
  let raw = null;
  const shown = [];
  for (let entry = 0; entry < 5; entry += 1) {
    const state = read(raw);
    shown.push(due(state));
    if (due(state)) raw = counted(state);
  }
  assert.deepEqual(shown, [true, true, true, false, false]);
  assert.equal(MOST, 3);
  assert.equal(KEY, "aa-full-tip");
  assert.equal(due(read(hidden())), false);
});

test("what storage holds is read leniently", () => {
  for (const raw of [null, "", "nonsense", "[]", '{"shown":-1}', '{"shown":"2"}', '{"shown":1.5}']) {
    assert.deepEqual(read(raw), { shown: 0, hidden: false }, raw);
  }
  assert.deepEqual(read('{"shown":2}'), { shown: 2, hidden: false });
  assert.deepEqual(read('{"hidden":true}'), { shown: 0, hidden: true });
});

test("the tip goes above the button, else under it, inside the window", () => {
  const view = { width: 390, height: 800 };
  const tip = { width: 260, height: 100 };
  const box = (left, top) => ({ left, top, right: left + 44, bottom: top + 44 });
  // Right edge, low: above, right edges in line.
  assert.deepEqual(place(box(338, 600), tip, view, "right", 8), { left: 122, top: 492 });
  // Left edge: left edges in line.
  assert.deepEqual(place(box(8, 600), tip, view, "left", 8), { left: 8, top: 492 });
  // Near the top: under the button.
  assert.deepEqual(place(box(338, 8), tip, view, "right", 8), { left: 122, top: 60 });
  // Dragged off to the middle of a narrow window: kept inside.
  assert.deepEqual(place(box(10, 300), { width: 380, height: 100 }, view, "right", 8), { left: 8, top: 192 });
  // No room either way: as far up as fits.
  assert.deepEqual(place(box(338, 40), tip, { width: 390, height: 150 }, "right", 8), { left: 122, top: 42 });
});
