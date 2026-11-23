// Zoom of the file alone, static/zoom.js: the math of a pinch, a double tap
// and Ctrl + wheel. Run: node --test tests/js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { clamp, pinchScale, keepPoint, toggled, wheelScale, isDoubleTap } = require(path.join(__dirname, "../../src/agent_artifacts/static/zoom.js"));

test("clamp keeps a value in its range", () => {
  assert.equal(clamp(0.5, 1, 4), 1);
  assert.equal(clamp(2.5, 1, 4), 2.5);
  assert.equal(clamp(9, 1, 4), 4);
});

test("a pinch scales by how far the fingers moved apart, from 1x to the most", () => {
  assert.equal(pinchScale(1, 100, 200, 4), 2);
  assert.equal(pinchScale(2, 100, 150, 4), 3);
  assert.equal(pinchScale(1, 100, 50, 4), 1); // never under 1x
  assert.equal(pinchScale(3, 100, 400, 4), 4); // never over the most
  assert.equal(pinchScale(2, 100, 50, 8), 1);
  // Fingers at one spot: the scale stays.
  assert.equal(pinchScale(2, 0, 100, 4), 2);
  assert.equal(pinchScale(2, 100, 0, 4), 2);
});

test("the point under the fingers stays under them", () => {
  // Content at (10, 20) in the window at 1x; fingers at (110, 220).
  const box = { left: 10, top: 20 };
  const focal = { x: 110, y: 220 };
  // Scaled 2x from its corner, not scrolled yet: the corner is where it was,
  // the point (100, 200) of the content is now at (210, 420).
  const d = keepPoint(box, 1, focal, box, 2, focal);
  assert.deepEqual(d, { dx: 100, dy: 200 });
  // After that scroll the corner is at (-90, -180): the point is back.
  const moved = { left: box.left - d.dx, top: box.top - d.dy };
  assert.equal(moved.left + 100 * 2, focal.x);
  assert.equal(moved.top + 200 * 2, focal.y);
  // Nothing changed: no scroll.
  assert.deepEqual(keepPoint(box, 2, focal, box, 2, focal), { dx: 0, dy: 0 });
  // The fingers moved 30 px right at the same scale: the content goes along.
  assert.deepEqual(keepPoint(box, 2, focal, box, 2, { x: 140, y: 220 }), { dx: -30, dy: 0 });
  // Back to 1x from 2x keeps the point too.
  const zoomed = { left: -90, top: -180 };
  const back = keepPoint(zoomed, 2, focal, zoomed, 1, focal);
  assert.equal(zoomed.left - back.dx + 100, focal.x);
  assert.equal(zoomed.top - back.dy + 200, focal.y);
});

test("a double tap zooms in to 2x and back to 1x", () => {
  assert.equal(toggled(1), 2);
  assert.equal(toggled(1.02), 2);
  assert.equal(toggled(2), 1);
  assert.equal(toggled(3.5), 1);
});

test("the second tap is close in time and place", () => {
  const first = { x: 100, y: 100, at: 1000 };
  assert.equal(isDoubleTap(first, { x: 110, y: 105, at: 1250 }), true);
  assert.equal(isDoubleTap(first, { x: 110, y: 105, at: 1400 }), false); // too late
  assert.equal(isDoubleTap(first, { x: 160, y: 100, at: 1100 }), false); // too far
  assert.equal(isDoubleTap(null, { x: 100, y: 100, at: 1100 }), false);
});

test("Ctrl + wheel: down zooms out, up zooms in, within the range", () => {
  assert.ok(wheelScale(2, -100, 0, 4) > 2);
  assert.ok(wheelScale(2, 100, 0, 4) < 2);
  assert.equal(wheelScale(1, 100, 0, 4), 1);
  assert.equal(wheelScale(4, -100, 0, 4), 4);
  // A line notch is about as much as a pixel one of 33 px.
  assert.equal(wheelScale(2, -1, 1, 4), wheelScale(2, -33, 0, 4));
  // One huge turn is one notch at most.
  assert.equal(wheelScale(2, -5000, 0, 8), wheelScale(2, -100, 0, 8));
});
