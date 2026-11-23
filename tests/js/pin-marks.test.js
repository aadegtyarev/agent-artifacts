// static/pin-marks.js, shared by the viewer and the script in a page: the
// dot's colour the frame takes from the viewer's message. Run: node --test tests/js
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");

// A plain script, as pages.py puts it into a page: no exports.
const source = fs.readFileSync(path.join(__dirname, "../../src/agent_artifacts/static/pin-marks.js"), "utf8");
const pinMarks = new Function(`${source}\nreturn pinMarks;`)();

test("the dot's colour is one of the list, by its name", () => {
  assert.equal(pinMarks.dotColour("red"), "#cf291d");
  assert.equal(pinMarks.dotColour("blue"), "#0a64d8");
  assert.equal(pinMarks.dotColour("green"), "#1e7d36");
});

test("anything else from the viewer's message is red", () => {
  const sly = { toString: () => "blue" };
  for (const v of [undefined, null, "", "Blue", " blue", "#00ff00", "purple", "__proto__", "toString",
    "constructor", "hasOwnProperty", ["blue"], sly, 1, true]) {
    assert.equal(pinMarks.dotColour(v), "#cf291d", String(v));
  }
});
