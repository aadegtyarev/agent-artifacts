// Links between files, static/links.js: which links in a page open another
// page of the service, and what the viewer opens for them. Run: node --test tests/js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const { target, destination, fragmentOf } = require(path.join(__dirname, "../../src/agent_artifacts/static/links.js"));

const ID = "AbCdEfGhIjKlMnOpQrSt_-";
const OTHER = "ZzYyXxWwVvUuTtSsRrQq09";
// The page's own address in the viewer's frame.
const BASE = `https://art.example.org/a/${ID}/raw`;

test("a link to a file or a project of the service, absolute or by path", () => {
  assert.deepEqual(target(`/a/${OTHER}`, BASE), { path: `/a/${OTHER}`, fragment: "" });
  assert.deepEqual(target(`/p/${OTHER}#mockups`, BASE), { path: `/p/${OTHER}`, fragment: "mockups" });
  assert.deepEqual(target(`https://art.example.org/a/${OTHER}#s-2`, BASE), { path: `/a/${OTHER}`, fragment: "s-2" });
  assert.deepEqual(target(`../../a/${OTHER}`, BASE), { path: `/a/${OTHER}`, fragment: "" });
});

test("anything else is not one", () => {
  for (const href of [
    "#top", "", `/a/${OTHER}/raw`, `/a/${OTHER}/download`, `/a/${OTHER}?x=1`, `/a/${OTHER}/`,
    `/a/${OTHER}x`, "/a/short", `/x/${OTHER}`, `https://other.example.org/a/${OTHER}`,
    `http://art.example.org/a/${OTHER}`, `https://art.example.org:8443/a/${OTHER}`,
    `javascript:alert(1)`, `data:text/html,x`, "http://[bad",
  ]) {
    assert.equal(target(href, BASE), null, href);
  }
  // A page whose own address has no origin of its own.
  assert.equal(target(`/a/${OTHER}`, "data:text/html,x"), null);
});

test("the viewer builds the address on its own origin from checked parts", () => {
  const ORIGIN = "https://art.example.org";
  assert.equal(destination(`/a/${OTHER}`, "", ORIGIN), `${ORIGIN}/a/${OTHER}`);
  assert.equal(destination(`/p/${OTHER}`, "section-2.a:b_c", ORIGIN), `${ORIGIN}/p/${OTHER}#section-2.a:b_c`);
  assert.equal(destination(`/a/${OTHER}`, "x".repeat(64), ORIGIN), `${ORIGIN}/a/${OTHER}#${"x".repeat(64)}`);
});

test("the viewer refuses anything else", () => {
  const ORIGIN = "https://art.example.org";
  for (const [p, f] of [
    [`/a/${OTHER}/raw`, ""], [`//evil.example/a/${OTHER}`, ""], [`/a/${OTHER}?x`, ""],
    [`https://evil.example/a/${OTHER}`, ""], [`/a/${OTHER}\n`, ""], [`/a/${OTHER}`, "x".repeat(65)],
    [`/a/${OTHER}`, "a b"], [`/a/${OTHER}`, "#x"], [`/a/${OTHER}`, "%41"], [`/a/${OTHER}`, "заголовок"],
    [`/a/${OTHER}`, undefined], [undefined, ""], [{}, ""], [`/a/${OTHER}`, 1],
  ]) {
    assert.equal(destination(p, f, ORIGIN), null, JSON.stringify([p, f]));
  }
});

test("the viewer's own fragment is passed on only when it is as expected", () => {
  assert.equal(fragmentOf("#intro"), "intro");
  assert.equal(fragmentOf(""), "");
  assert.equal(fragmentOf("#a b"), "");
  assert.equal(fragmentOf("#%D0%B0"), "");
});
