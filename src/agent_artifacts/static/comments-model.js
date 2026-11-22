// What the viewer shows, derived from the comments and drafts alone: no DOM,
// no network, so node can load it for the tests in tests/js. The viewer
// loads it as its own script before comments.js, which keeps the state in
// `S` and calls this with it.
//   state:   { comments, drafts }, read on every call (they are replaced)
//   pending: ids of drafts deleted but not yet sent (a Map or a Set)
//   seen:    replies of others seen in this browser, per comment number
//   now:     the clock, for the tests
const commentsModel = (state, { pending = new Set(), seen = {}, now = Date.now } = {}) => {
  const LETTERS = "abcdefghijklmnopqrstuvwxyz";
  const letter = (i) => LETTERS[i % 26] + (i >= 26 ? String(Math.floor(i / 26) + 1) : "");
  const liveDrafts = () => state.drafts.filter((d) => !pending.has(d.id));
  const draftById = (id) => state.drafts.find((d) => d.id === id) || null;

  // Drafts are of kinds: a new comment, or a reply, an approval, a
  // rejection or an acceptance about published comment `of`.
  const isComment = (d) => d.kind === "comment";
  const commentDrafts = () => liveDrafts().filter(isComment);
  const actsOn = (n) => liveDrafts().filter((d) => !isComment(d) && d.of === n);
  // A reply or decision about a comment that is gone (closed meanwhile) is
  // left out; the server drops it when the review is finished.
  const here = (d) => state.comments.some((c) => c.n === d.of);
  const decisionOn = (n) => actsOn(n).find((d) => d.kind !== "reply") || null;
  const repliesOn = (n) => actsOn(n).filter((d) => d.kind === "reply");
  // What the review holds: new comments, and comments decided or replied to.
  function reviewCount() {
    const touched = new Set(liveDrafts().filter((d) => !isComment(d) && here(d)).map((d) => d.of));
    return commentDrafts().length + touched.size;
  }

  // Replies of others seen in this browser, per comment, for the red dot.
  const othersReplies = (c) => c.replies.filter((r) => !r.mine && !r.agent).length;
  // True when it changed, so the caller keeps it.
  function markSeen(c) {
    if (othersReplies(c) === (seen[c.n] || 0)) return false;
    seen[c.n] = othersReplies(c);
    return true;
  }

  // A comment's pin: the fill says its stage, the badge what is next.
  const AGENT = { waiting: "wait", working: "work", done: "fin", wrote: "wrote", asked: "ask" };
  function stateOf(c) {
    // Awaiting approval: someone else's comment nothing was asked of the
    // agent about yet: no `agent` state (the agent's own reply to an
    // only-published comment sets it too).
    const pend = c.mine !== true && !c.agent && !c.approved_by && !c.rejected_by;
    const fill = c.rejected_by ? "rej" : pend ? "pend" : "solid";
    const dec = decisionOn(c.n);
    let badge = "";
    if (dec) badge = dec.kind === "reject" ? "no" : "ok";
    else if (repliesOn(c.n).length) badge = "re";
    else if (othersReplies(c) > (seen[c.n] || 0)) badge = "new";
    else if (AGENT[c.agent]) badge = AGENT[c.agent];
    return { fill, badge, pend };
  }

  // The person's new drafts, then the published comments. Drafts stay in
  // sight with comment mode off, so the page can be used (another screen of
  // an app, say) without them disappearing.
  function items() {
    const list = [];
    commentDrafts().forEach((d, i) => list.push({
      key: `d${d.id}`, draft: true, own: true, label: letter(i), anchor: d.anchor, text: d.text, d,
      fill: "draft", badge: "",
    }));
    for (const c of state.comments) {
      const st = stateOf(c);
      list.push({ key: `c${c.n}`, draft: false, own: c.mine === true, label: String(c.n),
        anchor: c.anchor, text: c.text, c, fill: st.fill, badge: st.badge, pend: st.pend });
    }
    return list;
  }
  const itemOf = (key) => items().find((i) => i.key === key) || null;

  // The agent's comments by state, and the latest review sent to it. A
  // review publishes at one moment, so the moments number the reviews.
  function agentCounts() {
    const n = { waiting: 0, working: 0, done: 0, wrote: 0, asked: 0 };
    for (const c of state.comments) if (c.agent in n) n[c.agent] += 1;
    return n;
  }
  function lastSent() {
    const times = [...new Set(state.comments.filter((c) => c.agent).map((c) => c.created_at))].sort();
    if (!times.length) return null;
    const at = new Date(times[times.length - 1]);
    const hm = at.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
    return { at: now() - at.getTime() < 86400000 ? hm : `${when(at.toISOString())}, ${hm}` };
  }

  function when(iso) {
    const t = new Date(iso);
    const seconds = (now() - t.getTime()) / 1000;
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
    const options = { month: "short", day: "numeric" };
    if (t.getFullYear() !== new Date(now()).getFullYear()) options.year = "numeric";
    return t.toLocaleDateString("en", options);
  }

  // A row's second line: what is next first (coloured), then whose, how
  // many replies or when. `lost`: where its pin is when it is not on the
  // content shown, or null.
  const LEADS = {
    waiting: [null, "with the agent"], working: ["work", "Agent is working"],
    done: ["fin", "Done · check it"], wrote: ["fin", "Done, wrote"], asked: ["ask", "Asks you"],
  };
  function status(it, lost = null) {
    if (it.draft) return [null, lost ? `your draft · ${lost}` : "your draft"];
    const c = it.c;
    const dec = decisionOn(c.n);
    const mine = repliesOn(c.n).length;
    let lead = null;
    if (dec) lead = ["mine", { approve: "✓ Approve", reject: "✕ Reject", accept: "✓ Accept" }[dec.kind]];
    else if (mine) lead = ["mine", "↩ Your reply"];
    else if (c.rejected_by) lead = [null, "rejected"];
    else if (it.pend) lead = [null, "awaiting approval"];
    else if (LEADS[c.agent]) lead = LEADS[c.agent];
    if (dec && mine) lead = ["mine", `${lead[1]} · your reply`];
    const parts = [c.author];
    if (it.own) parts.push("you");
    const replies = c.replies.length;
    parts.push(replies ? `${replies} ${replies === 1 ? "reply" : "replies"}` : when(c.created_at));
    if (lost) parts.push(lost);
    return [lead, parts.join(" · ")];
  }

  return {
    letter, liveDrafts, draftById, isComment, commentDrafts, actsOn, decisionOn, repliesOn,
    reviewCount, othersReplies, markSeen, stateOf, items, itemOf, agentCounts, lastSent, when, status,
  };
};

if (typeof module !== "undefined") module.exports = commentsModel;
