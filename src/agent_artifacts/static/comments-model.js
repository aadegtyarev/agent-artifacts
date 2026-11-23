// What the viewer shows, derived from the comments and drafts alone: no DOM,
// no network, so node can load it for the tests in tests/js. The viewer
// loads it as its own script before comments.js, which keeps the state in
// `S` and calls this with it.
//   state:   { comments, drafts }, read on every call (they are replaced)
//   pending: ids of drafts deleted but not yet sent (a Map or a Set)
//   seen:    replies of others seen in this browser, per comment number
//   now:     the clock, for the tests
const commentsModel = (state, { pending = new Set(), seen = {}, now = Date.now } = {}) => {
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
    if (othersReplies(c) <= (seen[c.n] || 0)) return false;
    seen[c.n] = othersReplies(c);
    return true;
  }

  const unseen = (c) => othersReplies(c) > (seen[c.n] || 0);

  // A comment's pin. The tone says whose it is: "own" (blue: written or
  // accepted here), "other" (orange: a colleague's), "agent" (purple: the
  // agent has it). The fill says whether it is done: "line" not yet,
  // "solid" done, "rej" rejected. Inside, the author's first letter, or
  // STAR while it is the agent's work. One badge only: "new", a red dot
  // for a colleague's unread reply or the agent's question.
  const STAR = "*";
  // A name starting with the star's sign does not pass for the agent.
  const initial = (name) => {
    const first = ([...String(name || "").trim()][0] || "?").toUpperCase();
    return first === STAR ? "?" : first;
  };
  function stateOf(c) {
    // Awaiting approval: someone else's comment nothing was asked of the
    // agent about yet: no `agent` state (the agent's own reply to an
    // only-published comment sets it too).
    const pend = c.mine !== true && !c.agent && !c.approved_by && !c.rejected_by;
    const dec = decisionOn(c.n);
    const answered = c.agent === "done" || c.agent === "wrote" || c.agent === "asked";
    let tone = c.mine === true || c.approved_by ? "own" : "other";
    let fill = "solid";
    let label = initial(c.author);
    let badge = "";
    // The person's decision in this review shows at once, in blue.
    if (dec && dec.kind === "reject") fill = "rej";
    else if (dec && dec.kind === "accept") [tone, label] = ["own", STAR];
    else if (dec) tone = "own";
    else if (answered && repliesOn(c.n).length) [tone, fill, label] = ["own", "line", STAR];
    else if (c.rejected_by) fill = "rej";
    else if (c.agent) {
      [tone, label] = ["agent", STAR];
      if (c.agent !== "done" && c.agent !== "wrote") fill = "line";
      if (c.agent === "asked") badge = "new";
    } else if (pend) fill = "line";
    if (unseen(c)) badge = "new";
    return { tone, fill, label, badge, pend };
  }

  // The person's new drafts, then the published comments. Drafts stay in
  // sight with comment mode off, so the page can be used (another screen of
  // an app, say) without them disappearing.
  function items() {
    const list = [];
    commentDrafts().forEach((d) => list.push({
      key: `d${d.id}`, draft: true, tone: "own", label: initial(d.author),
      anchor: d.anchor, text: d.text, d, fill: "line", badge: "",
    }));
    for (const c of state.comments) {
      const st = stateOf(c);
      list.push({ key: `c${c.n}`, draft: false, tone: st.tone, label: st.label,
        anchor: c.anchor, text: c.text, c, fill: st.fill, badge: st.badge, pend: st.pend });
    }
    return list;
  }
  const itemOf = (key) => items().find((i) => i.key === key) || null;

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
    waiting: [null, "sent, not taken yet"], working: ["work", "Agent is working"],
    done: ["fin", "Done · check it"], wrote: ["fin", "Done, wrote"], asked: ["ask", "Asks you"],
  };
  function status(it, lost = null) {
    // Under "Your drafts" a draft needs no word of its own: when, and where
    // when its pin is not on what is shown.
    if (it.draft) {
      const at = it.d.created_at ? when(it.d.created_at) : "";
      return [null, [at, lost].filter(Boolean).join(" · ")];
    }
    const c = it.c;
    const dec = decisionOn(c.n);
    const mine = repliesOn(c.n).length;
    let lead = null;
    if (dec) lead = ["mine", { approve: "✓ Accept", reject: "✕ Reject", accept: "✓ Accept" }[dec.kind]];
    else if (mine) lead = ["mine", "↩ Your reply"];
    else if (!c.rejected_by && c.mine === true && unseen(c)) lead = ["ask", "New reply"];
    else if (c.rejected_by) lead = [null, "rejected"];
    else if (it.pend) lead = [null, "awaiting approval"];
    else if (LEADS[c.agent]) lead = LEADS[c.agent];
    if (dec && mine) lead = ["mine", `${lead[1]} · your reply`];
    const parts = [c.author];
    if (it.c.mine) parts.push("you");
    const replies = c.replies.length;
    parts.push(replies ? `${replies} ${replies === 1 ? "reply" : "replies"}` : when(c.created_at));
    if (lost) parts.push(lost);
    return [lead, parts.join(" · ")];
  }

  // What waits for the person's yes, with neither a decision nor a reply of
  // theirs yet, as [comment, kind]: the agent's finished work (done, or done
  // and wrote) to accept, and colleagues' comments awaiting approval to
  // approve. The agent's questions are left to be answered one by one.
  function acceptable() {
    const out = [];
    for (const it of items()) {
      if (!it.c || actsOn(it.c.n).length) continue;
      if (it.c.agent === "done" || it.c.agent === "wrote") out.push([it.c, "accept"]);
      else if (it.pend) out.push([it.c, "approve"]);
    }
    return out;
  }
  // One waiting is accepted on its own row; Accept all shows from two.
  const acceptAllShown = () => acceptable().length >= 2;

  // The list by what each item waits for, in this order; empty groups are
  // left out. "need": what the person decides: the agent's finished work,
  // then its questions, then colleagues' comments awaiting approval.
  // "drafts": this review: new comments, and comments decided or replied to.
  // "agent": sent and not answered yet. "talk": published, nothing waits
  // for this person. "closed": rejected (accepted ones leave the viewer).
  const GROUPS = [
    ["need", "Need you"], ["drafts", "Your drafts"], ["agent", "In progress"],
    ["talk", "Discussion"], ["closed", "Closed"],
  ];
  function groupOf(it) {
    if (it.draft) return "drafts";
    const c = it.c;
    // A reply drafted on a rejected comment is part of the review.
    if (actsOn(c.n).length) return "drafts";
    if (c.rejected_by) return "closed";
    if (c.agent === "done" || c.agent === "wrote" || c.agent === "asked" || it.pend
      || (c.mine === true && unseen(c))) return "need";
    if (c.agent === "waiting" || c.agent === "working") return "agent";
    return "talk";
  }
  // Inside "need": done first, then asked, then colleagues'.
  const needRank = (it) => (it.c.agent === "done" || it.c.agent === "wrote" ? 0 : it.c.agent === "asked" ? 1 : 2);
  function groups() {
    const all = items();
    return GROUPS.map(([id, title]) => {
      const list = all.filter((it) => groupOf(it) === id);
      // sort is stable: the same rank keeps the comments' order.
      if (id === "need") list.sort((a, b) => needRank(a) - needRank(b));
      return { id, title, items: list };
    }).filter((g) => g.items.length);
  }
  // The items in the list's order, for ‹ › on one item.
  const ordered = () => groups().flatMap((g) => g.items);
  // The items in an order kept from before (keys), so a decision made on
  // one item does not move it while the person steps through them: the
  // ones gone are left out, new ones come after in the list's order.
  function keptOrder(keys) {
    const current = ordered();
    const byKey = new Map(current.map((it) => [it.key, it]));
    const kept = keys.filter((k) => byKey.has(k)).map((k) => byKey.get(k));
    return [...kept, ...current.filter((it) => !keys.includes(it.key))];
  }
  // How many items each group holds, for the strip.
  function groupCounts() {
    const n = Object.fromEntries(GROUPS.map(([id]) => [id, 0]));
    for (const it of items()) n[groupOf(it)] += 1;
    return n;
  }

  return {
    STAR, initial, liveDrafts, draftById, isComment, commentDrafts, actsOn, decisionOn, repliesOn,
    reviewCount, othersReplies, markSeen, stateOf, items, itemOf, when, status,
    acceptable, acceptAllShown, groups, ordered, keptOrder, groupCounts,
  };
};

if (typeof module !== "undefined") module.exports = commentsModel;
