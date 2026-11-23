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
  assert.deepEqual(m.stateOf(comment(1)), { tone: "other", fill: "line", label: "B", badge: "", pend: true });
  assert.deepEqual(m.status(itemFor(m, 1))[0], [null, "awaiting approval"]);
});

test("a colleague's only-published comment the agent answered is not pending", () => {
  // The agent's reply sets `agent` alone; `approved_by` stays empty.
  const m = model([comment(1, { agent: "wrote" })]);
  assert.deepEqual(m.stateOf(comment(1, { agent: "wrote" })),
    { tone: "agent", fill: "solid", label: "*", badge: "", pend: false });
  assert.deepEqual(m.status(itemFor(m, 1))[0], ["fin", "Done, wrote"]);
});

test("own, approved and rejected comments do not await approval", () => {
  const m = model([]);
  assert.equal(m.stateOf(comment(1, { mine: true })).pend, false);
  assert.equal(m.stateOf(comment(1, { approved_by: "alice" })).pend, false);
  assert.deepEqual(m.stateOf(comment(1, { rejected_by: "alice" })),
    { tone: "other", fill: "rej", label: "B", badge: "", pend: false });
  // A name that starts with the star's sign gets no star.
  assert.equal(m.stateOf(comment(1, { author: "*bob" })).label, "?");
  // Accepted, a colleague's comment is blue: it is in force here.
  assert.equal(m.stateOf(comment(1, { approved_by: "alice" })).tone, "own");
  const rejected = model([comment(1, { rejected_by: "alice" })]);
  assert.deepEqual(rejected.status(itemFor(rejected, 1))[0], [null, "rejected"]);
});

test("a decision and a reply on one comment: the decision leads, the reply is noted", () => {
  const m = model([comment(1), comment(2), comment(3)], [
    draft("approve", 1), draft("reply", 1), draft("reply", 2), draft("reject", 3),
  ]);
  assert.deepEqual([itemFor(m, 1).tone, itemFor(m, 1).fill], ["own", "solid"]);
  assert.deepEqual(m.status(itemFor(m, 1))[0], ["mine", "✓ Accept · your reply"]);
  assert.deepEqual([itemFor(m, 2).tone, itemFor(m, 2).fill], ["other", "line"]);
  assert.deepEqual(m.status(itemFor(m, 2))[0], ["mine", "↩ Your reply"]);
  assert.equal(itemFor(m, 3).fill, "rej");
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

test("the agent's pins: its star, outlined until done; decisions in blue; one red dot", () => {
  const replied = { replies: [{ mine: false, agent: false }] };
  const seen = {};
  const m = model([
    comment(1, { agent: "working" }),
    comment(2, { agent: "done" }),
    comment(3, { agent: "asked" }),
    comment(4, { agent: "done" }),
    comment(5, { agent: "done" }),
    comment(6, { mine: true, ...replied }),
  ], [draft("accept", 4), draft("reply", 5)], { seen });
  const pin = (n) => { const it = itemFor(m, n); return [it.tone, it.fill, it.label, it.badge]; };
  assert.deepEqual(pin(1), ["agent", "line", "*", ""]);
  assert.deepEqual(pin(2), ["agent", "solid", "*", ""]);
  assert.deepEqual(pin(3), ["agent", "line", "*", "new"]); // it asks
  assert.deepEqual(pin(4), ["own", "solid", "*", ""]); // accepted here
  assert.deepEqual(pin(5), ["own", "line", "*", ""]); // sent back with a reply
  assert.deepEqual(pin(6), ["own", "solid", "B", "new"]); // a colleague replied
  // Seen once, the colleague's reply no longer shows the dot.
  assert.equal(m.markSeen(itemFor(m, 6).c), true);
  assert.equal(m.markSeen(itemFor(m, 6).c), false);
  assert.deepEqual(seen, { 6: 1 });
  assert.equal(itemFor(m, 6).badge, "");
});

test("replies of the agent or one's own do not count as unseen", () => {
  const m = model([comment(1, { agent: "wrote", replies: [{ mine: true }, { agent: true }] })]);
  assert.equal(itemFor(m, 1).badge, "");
  assert.equal(m.othersReplies(itemFor(m, 1).c), 0);
});

test("status leads per agent state, and the rest of the line", () => {
  const leads = {
    waiting: [null, "sent, not taken yet"], working: ["work", "Agent is working"],
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

test("drafts: listed first, marked with the author's letter, their status says when and where", () => {
  const drafts = Array.from({ length: 27 }, () => draft("comment"));
  const m = model([comment(5)], drafts);
  const all = m.items();
  assert.deepEqual(all.slice(0, 3).map((i) => i.key), drafts.slice(0, 3).map((d) => `d${d.id}`));
  assert.equal(all[0].label, "?"); // no author yet
  assert.equal(all[27].label, "B");
  assert.equal(m.itemOf(`d${drafts[1].id}`).fill, "line");
  assert.deepEqual(m.status(all[0]), [null, ""]);
  assert.deepEqual(m.status(all[0], "on another screen"), [null, "on another screen"]);
  const dated = model([], [draft("comment", null, { created_at: ago(120) })]);
  const it = dated.items()[0];
  assert.deepEqual(dated.status(it), [null, "2 min ago"]);
  assert.deepEqual(dated.status(it, "on another screen"), [null, "2 min ago · on another screen"]);
  assert.equal(m.itemOf("c99"), null);
});

test("Accept all picks the agent's finished work to accept and colleagues' pending comments to approve", () => {
  const m = model([
    comment(1, { agent: "done" }),
    comment(2, { agent: "wrote", mine: true }), // the agent's answer on one's own: accepted too
    comment(3), // a colleague's, awaiting approval
    comment(4, { agent: "asked" }), // a question is answered one by one
    comment(5, { agent: "working" }),
    comment(6, { mine: true }), // one's own is never approved
    comment(7, { approved_by: "alice" }),
    comment(8, { rejected_by: "alice" }),
  ]);
  assert.deepEqual(m.acceptable().map(([c, kind]) => [c.n, kind]), [[1, "accept"], [2, "accept"], [3, "approve"]]);
  assert.equal(m.acceptAllShown(), true);
});

test("Accept all skips a comment with a decision or a reply draft of the person's", () => {
  const comments = [comment(1, { agent: "done" }), comment(2), comment(3, { agent: "done" }), comment(4)];
  const m = model(comments, [draft("reject", 1), draft("reply", 2), draft("comment")]);
  assert.deepEqual(m.acceptable().map(([c, kind]) => [c.n, kind]), [[3, "accept"], [4, "approve"]]);
  // A draft being deleted no longer counts.
  const undone = draft("approve", 4);
  const pending = model(comments, [undone], { pending: new Set([undone.id]) });
  assert.equal(pending.acceptable().length, 4);
});

test("Accept all shows from two waiting", () => {
  assert.equal(model([]).acceptAllShown(), false);
  assert.equal(model([comment(1, { agent: "done" })]).acceptAllShown(), false);
  assert.equal(model([comment(1, { agent: "done" }), comment(2)]).acceptAllShown(), true);
  assert.equal(model([comment(1, { agent: "done" }), comment(2)], [draft("approve", 2)]).acceptAllShown(), false);
});

test("the list groups by what waits for the person, in a fixed order", () => {
  const m = model([
    comment(1), // a colleague's, awaiting approval
    comment(2, { agent: "asked", mine: true }),
    comment(3, { agent: "working", mine: true }),
    comment(4, { agent: "done", mine: true }),
    comment(5, { mine: true }), // published, nothing waits
    comment(6, { rejected_by: "alice" }),
    comment(8, { agent: "waiting", mine: true }),
    comment(9, { agent: "wrote" }),
    comment(10), // decided in this review: a draft, not waiting
    comment(11, { approved_by: "alice" }), // approved and not sent: discussion
  ], [draft("comment"), draft("approve", 10)]);
  const shape = m.groups().map((g) => [g.id, g.items.map((i) => i.key)]);
  assert.deepEqual(shape, [
    ["need", ["c4", "c9", "c2", "c1"]],
    ["drafts", [`d${nextId - 2}`, "c10"]],
    ["agent", ["c3", "c8"]],
    ["talk", ["c5", "c11"]],
    ["closed", ["c6"]],
  ]);
  assert.deepEqual(m.ordered().map((i) => i.key), shape.flatMap(([, keys]) => keys));
  assert.deepEqual(m.groupCounts(), { need: 4, drafts: 2, agent: 2, talk: 2, closed: 1 });
});

test("empty groups are left out", () => {
  const m = model([comment(1, { mine: true })]);
  assert.deepEqual(m.groups().map((g) => g.id), ["talk"]);
  assert.deepEqual(model([]).groups(), []);
});

test("a rejected comment with a reply draft of the person's is in the drafts, as the strip counts it", () => {
  const m = model([comment(1, { rejected_by: "alice" })], [draft("reply", 1)]);
  assert.deepEqual(m.groups().map((g) => [g.id, g.items.map((i) => i.key)]), [["drafts", ["c1"]]]);
  assert.equal(m.reviewCount(), 1);
});

test("a kept order holds after a decision moves an item; gone ones drop, new ones follow", () => {
  const state = { comments: [comment(1, { agent: "done" }), comment(2), comment(3, { mine: true })], drafts: [] };
  const m = commentsModel(state, { now: () => NOW });
  const keys = m.ordered().map((i) => i.key);
  assert.deepEqual(keys, ["c1", "c2", "c3"]);
  // Accepting c1 moves it to the drafts group in the list's order...
  state.drafts = [draft("accept", 1)];
  assert.deepEqual(m.ordered().map((i) => i.key), ["c2", "c1", "c3"]);
  // ...but stepping keeps the order it started with.
  assert.deepEqual(m.keptOrder(keys).map((i) => i.key), ["c1", "c2", "c3"]);
  state.comments = state.comments.filter((c) => c.n !== 3).concat(comment(4));
  assert.deepEqual(m.keptOrder(keys).map((i) => i.key), ["c1", "c2", "c4"]);
});

test("unread peer replies promote only my thread and acknowledgement persists", () => {
  const peer = { author: "Bob", mine: false, agent: false };
  const own = comment(1, { mine: true, replies: [peer, { mine: true }] });
  const other = comment(2, { replies: [peer] });
  const seen = {};
  const m = model([own, other], [], { seen });
  assert.equal(m.groups()[0].items.some((it) => it.key === "c1"), true);
  assert.deepEqual(m.status(itemFor(m, 1))[0], ["ask", "New reply"]);
  assert.equal(m.groupCounts().need, 2);
  assert.equal(m.acceptable().length, 1);
  assert.equal(m.markSeen(own), true);
  assert.equal(model([own], [], { seen }).groupCounts().talk, 1);
  assert.equal(model([own], [draft("reply", 1)], {}).groups()[0].id, "drafts");
  const closed = model([comment(3, { mine: true, rejected_by: "x", replies: [peer] })]);
  assert.equal(closed.groups()[0].id, "closed");
  const later = model([{ ...own, replies: [...own.replies, peer] }], [], { seen });
  assert.equal(later.groupCounts().need, 1);
});

test("an approved colleague thread keeps its unread pin outside Need you", () => {
  const m = model([comment(1, { approved_by: "Alice", replies: [{ mine: false, agent: false }] })]);
  assert.equal(itemFor(m, 1).badge, "new");
  assert.equal(m.groupCounts().need, 0);
  assert.equal(m.groups()[0].id, "talk");
});


test("agent proposals keep their star and group through approval and replies", () => {
  for (const [extra, group, acceptable] of [
    [{}, "need", true],
    [{ agent: "waiting" }, "agent", false],
    [{ agent: "working" }, "agent", false],
    [{ agent: "done" }, "need", true],
    [{ agent: "wrote", approved_by: "Pat" }, "talk", false],
    [{ agent: "asked", approved_by: "Pat" }, "need", false],
    [{ approved_by: "Pat" }, "talk", false],
  ]) {
    const c = comment(1, { author: "agent", by_agent: true, ...extra });
    const m = model([c]);
    assert.equal(itemFor(m, 1).label, "*");
    assert.equal(m.groups().find(g => g.items.length).id, group);
    assert.equal(m.acceptable().length, acceptable ? 1 : 0);
    if (acceptable) assert.equal(m.acceptable()[0][1], "approve");
  }
  assert.notEqual(model([comment(1, { author: "agent" })]).itemOf("c1").label, "*");
});
