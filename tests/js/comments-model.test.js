// The viewer's state model, static/comments-model.js: what a pin, a row and
// the strip show for given comments and drafts. Run: node --test tests/js
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const commentsModel = require(path.join(__dirname, "../../src/agent_artifacts/static/comments-model.js"));

const NOW = Date.parse("2026-11-22T12:00:00Z");
const ago = (seconds) => new Date(NOW - seconds * 1000).toISOString();

// A published comment as GET /comments gives it: a colleague's by default.
const comment = (n, extra = {}) => ({
  n, author: "Bob", text: `comment ${n}`, anchor: null, mine: false, agent: null,
  approved_by: null, rejected_by: null, replies: [], created_at: ago(30), ...extra,
});
let nextId = 1;
const draft = (kind, of = null, extra = {}) => ({ id: nextId++, kind, of, text: "x", anchor: null, ...extra });

function model(comments, drafts = [], opts = {}) {
  const state = { comments, drafts };
  return commentsModel(state, { now: () => NOW, ...opts });
}
const itemFor = (m, n) => m.itemOf(`c${n}`);

test("a colleague's comment nothing went to the agent about awaits approval", () => {
  const m = model([comment(1)]);
  assert.deepEqual(m.stateOf(comment(1)), { fill: "pend", badge: "", pend: true });
  assert.deepEqual(m.status(itemFor(m, 1))[0], [null, "awaiting approval"]);
});

test("a colleague's only-published comment the agent answered is not pending", () => {
  // The agent's reply sets `agent` alone; `approved_by` stays empty.
  const m = model([comment(1, { agent: "wrote" })]);
  assert.deepEqual(m.stateOf(comment(1, { agent: "wrote" })), { fill: "solid", badge: "wrote", pend: false });
  assert.deepEqual(m.status(itemFor(m, 1))[0], ["fin", "Done, wrote"]);
});

test("own, approved and rejected comments do not await approval", () => {
  const m = model([]);
  assert.equal(m.stateOf(comment(1, { mine: true })).pend, false);
  assert.equal(m.stateOf(comment(1, { approved_by: "alice" })).pend, false);
  assert.deepEqual(m.stateOf(comment(1, { rejected_by: "alice" })), { fill: "rej", badge: "", pend: false });
  const rejected = model([comment(1, { rejected_by: "alice" })]);
  assert.deepEqual(rejected.status(itemFor(rejected, 1))[0], [null, "rejected"]);
});

test("a decision and a reply on one comment: the decision leads, the reply is noted", () => {
  const m = model([comment(1), comment(2), comment(3)], [
    draft("approve", 1), draft("reply", 1), draft("reply", 2), draft("reject", 3),
  ]);
  assert.equal(itemFor(m, 1).badge, "ok");
  assert.deepEqual(m.status(itemFor(m, 1))[0], ["mine", "✓ Approve · your reply"]);
  assert.equal(itemFor(m, 2).badge, "re");
  assert.deepEqual(m.status(itemFor(m, 2))[0], ["mine", "↩ Your reply"]);
  assert.equal(itemFor(m, 3).badge, "no");
  assert.deepEqual(m.status(itemFor(m, 3))[0], ["mine", "✕ Reject"]);
  // A decision outranks the comment's own stage in the lead.
  const accepted = model([comment(4, { agent: "done", rejected_by: "x" })], [draft("accept", 4)]);
  assert.deepEqual(accepted.status(itemFor(accepted, 4))[0], ["mine", "✓ Accept"]);
  assert.deepEqual(accepted.decisionOn(4).kind, "accept");
});

test("reviewCount: new comments plus comments touched, once each", () => {
  const m = model([comment(1), comment(2)], [
    draft("comment"), draft("comment"),
    draft("approve", 1), draft("reply", 1), // one comment, counted once
    draft("reply", 2),
  ]);
  assert.equal(m.reviewCount(), 4);
});

test("reviewCount ignores drafts about gone comments and drafts being deleted", () => {
  const gone = draft("reply", 9);
  const deleted = draft("comment");
  const pending = new Set([deleted.id]);
  const m = model([comment(1)], [gone, draft("approve", 9), deleted, draft("reply", 1)], { pending });
  assert.equal(m.reviewCount(), 1);
  assert.equal(m.liveDrafts().length, 3);
  assert.equal(m.draftById(deleted.id), deleted); // still there for its Undo
});

test("badge priority: decision > own reply > unseen colleague reply > agent state", () => {
  const replied = { replies: [{ mine: false, agent: false }] };
  const seen = {};
  const m = model([
    comment(1, { agent: "asked", ...replied }),
    comment(2, { agent: "asked", ...replied }),
    comment(3, { agent: "asked", ...replied }),
    comment(4, { agent: "asked" }),
  ], [draft("approve", 1), draft("reply", 1), draft("reply", 2)], { seen });
  assert.deepEqual(m.items().map((i) => i.badge), ["ok", "re", "new", "ask"]);
  // Seen once, the colleague's reply no longer shows the dot.
  assert.equal(m.markSeen(itemFor(m, 3).c), true);
  assert.equal(m.markSeen(itemFor(m, 3).c), false);
  assert.deepEqual(seen, { 3: 1 });
  assert.equal(itemFor(m, 3).badge, "ask");
});

test("replies of the agent or one's own do not count as unseen", () => {
  const m = model([comment(1, { agent: "wrote", replies: [{ mine: true }, { agent: true }] })]);
  assert.equal(itemFor(m, 1).badge, "wrote");
  assert.equal(m.othersReplies(itemFor(m, 1).c), 0);
});

test("agentCounts counts the states the strip reads and nothing else", () => {
  const m = model([
    comment(1, { agent: "waiting" }), comment(2, { agent: "waiting" }), comment(3, { agent: "working" }),
    comment(4, { agent: "done" }), comment(5, { agent: "wrote" }), comment(6, { agent: "asked" }),
    comment(7), comment(8, { agent: "other" }),
  ]);
  assert.deepEqual(m.agentCounts(), { waiting: 2, working: 1, done: 1, wrote: 1, asked: 1 });
  assert.deepEqual(model([]).agentCounts(), { waiting: 0, working: 0, done: 0, wrote: 0, asked: 0 });
});

test("status leads per agent state, and the rest of the line", () => {
  const leads = {
    waiting: [null, "with the agent"], working: ["work", "Agent is working"],
    done: ["fin", "Done · check it"], wrote: ["fin", "Done, wrote"], asked: ["ask", "Asks you"],
  };
  for (const [agent, lead] of Object.entries(leads)) {
    const m = model([comment(1, { agent })]);
    assert.deepEqual(m.status(itemFor(m, 1)), [lead, "Bob · just now"], agent);
  }
  const own = model([comment(1, { mine: true, author: "Ann", created_at: ago(300) })]);
  assert.deepEqual(own.status(itemFor(own, 1)), [null, "Ann · you · 5 min ago"]);
  const two = model([comment(1, { agent: "done", replies: [{ agent: true }, { mine: true }] })]);
  assert.deepEqual(two.status(itemFor(two, 1), "spot not found"),
    [["fin", "Done · check it"], "Bob · 2 replies · spot not found"]);
  const one = model([comment(1, { agent: "done", replies: [{ agent: true }] })]);
  assert.equal(one.status(itemFor(one, 1))[1], "Bob · 1 reply");
});

test("drafts: listed first with letters, their status says so", () => {
  const drafts = Array.from({ length: 27 }, () => draft("comment"));
  const m = model([comment(5)], drafts);
  const all = m.items();
  assert.deepEqual(all.slice(0, 3).map((i) => i.label), ["a", "b", "c"]);
  assert.equal(all[26].label, "a2");
  assert.equal(all[27].label, "5");
  assert.equal(m.itemOf(`d${drafts[1].id}`).fill, "draft");
  assert.deepEqual(m.status(all[0]), [null, "your draft"]);
  assert.deepEqual(m.status(all[0], "on another screen"), [null, "your draft · on another screen"]);
  assert.equal(m.itemOf("c99"), null);
});

test("lastSent: the latest moment a review reached the agent", () => {
  assert.equal(model([comment(1)]).lastSent(), null);
  const latest = ago(3600);
  const m = model([comment(1, { agent: "done", created_at: ago(7200) }), comment(2, { agent: "waiting", created_at: latest })]);
  const hm = new Date(latest).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  assert.deepEqual(m.lastSent(), { at: hm });
});
