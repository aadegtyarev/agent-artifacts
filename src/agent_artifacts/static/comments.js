// Comments on the viewer page. The header's bubble turns comment mode on and
// off; in comment mode a tap on the content places a draft pin and opens the
// sheet with a field. Drafts stay on the server, seen only by this browser's
// author key, until Finish review publishes them all. On a phone the sheet
// has three heights (strip, content, full); on a computer the same
// views sit in a panel at the right. Names and texts come from anyone with
// the link, so they go in as text nodes only, never as HTML.
(() => {
  const sheet = document.getElementById("comments");
  if (!sheet) return;
  const $ = (id) => document.getElementById(id);
  const artifact = sheet.dataset.artifact;
  const toggle = $("comments-toggle");
  const strip = $("sheet-strip");
  const stripText = $("strip-text");
  const grip = $("sheet-grip");
  const body = $("sheet-body");
  const foot = $("sheet-foot");
  const image = $("image");
  const imageBox = $("image-box");
  const pinLayer = $("pins");
  const frame = $("frame");
  const dialog = $("name-dialog");
  const nameInput = $("name-input");
  const toastBox = $("toast");
  const toastText = $("toast-text");
  const toastUndo = $("toast-undo");

  // How a comment is tied to the content: "point" on an image, "element"
  // on a page (in the frame, through pins.js), none for the whole file.
  const pinType = sheet.dataset.pin === "point" && image ? "point"
    : sheet.dataset.pin === "element" && frame ? "element" : null;
  const canPin = () => pinType === "point" || (pinType === "element" && page.ready);

  const desk = window.matchMedia("(min-width: 900px)");
  const UNDO_MS = 5000;
  const desktopPanel = () => desk.matches;
  function foldPanel(folded) {
    if (folded && sheet.contains(document.activeElement)) (document.body.classList.contains("full") ? $("float-button") : toggle).focus({ preventScroll: true });
    document.body.classList.toggle("comments-folded", folded);
    document.dispatchEvent(new CustomEvent("aa:sheet"));
  }
  document.addEventListener("aa:viewer-full", () => {
    foldPanel(desktopPanel() && !S.mode);
    render();
  });


  // --- Browser storage ---------------------------------------------------------
  // Missing or refused: the author key and the name last as long as the page,
  // and the viewer says that drafts will not outlast it.
  let stored = true;
  const storage = {
    get(key) {
      try { return localStorage.getItem(key); } catch { stored = false; return null; }
    },
    set(key, value) {
      try { localStorage.setItem(key, value); } catch { stored = false; }
    },
  };
  const NAME_KEY = "agent-artifacts:name";
  const AUTHOR_KEY = "agent-artifacts:author-key";
  let authorKey = storage.get(AUTHOR_KEY);
  if (!/^[A-Za-z0-9_-]{22}$/.test(authorKey || "")) {
    const bytes = crypto.getRandomValues(new Uint8Array(16));
    authorKey = btoa(String.fromCharCode(...bytes)).replace(/\+/g, "-").replace(/\//g, "_").slice(0, 22);
    storage.set(AUTHOR_KEY, authorKey);
    if (storage.get(AUTHOR_KEY) !== authorKey) stored = false;
  }
  const asAuthor = { "X-Author-Key": authorKey };
  // A name as the server takes it: one line without control or bidi
  // characters (they could disguise it), at most 64 code points.
  const cleanName = (raw) => [...String(raw)
    .replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069\ud800-\udfff]+/gu, " ")
    .replace(/\s+/g, " ").trim()].slice(0, 64).join("").trim();
  // null until asked; "" for No name (the server makes it "anonymous").
  let name = storage.get(NAME_KEY);
  if (name !== null) name = cleanName(name);

  // --- State -----------------------------------------------------------------
  const S = {
    mode: false,
    // list, item (the one `sel`), compose (a new draft), finish
    view: "list",
    // the sheet's height on a phone: strip, m (content), f (full)
    pos: "strip",
    sel: null,
    // the new draft: {anchor, retained}; its text is in `fields.compose`
    compose: null,
    // the id of the draft being edited
    edit: null,
    // Finish review sends to the agent, or only publishes
    toAgent: true,
    // the list's folded groups (Discussion, Closed) opened by a tap
    opened: new Set(),
    // the list's order of keys while one item is shown, so ‹ › keep it
    // after a decision moves the item to another group; null on the list
    order: null,
    comments: [],
    loaded: false,
    // drafts: the server's, with `gone` while a Delete waits for its Undo
    drafts: [],
  };
  // Every change bumps it, so an older GET cannot overwrite a newer state.
  let generation = 0;
  // Drafts deleted but not yet sent: id -> timer.
  const pending = new Map();

  // --- The server ----------------------------------------------------------------
  class ApiError extends Error {
    constructor(status, code, nameRejected = false) {
      super();
      this.status = status;
      this.code = code;
      this.nameRejected = nameRejected;
    }
  }

  // Only these exact validation responses reopen the name prompt; none is displayed.
  const refusedNames = new Set([
    "author must be one line",
    "author must be text without control characters",
    "author is longer than 64 characters",
  ]);
  function errorCopy(e, action) {
    let next = "Try again.";
    if (e.status === 0) next = "Check the connection and try again.";
    else if (e.status === 429) next = "Wait a minute and try again.";
    else if (e.status === 404) next = "It may no longer be available. Refresh and try again.";
    else if (e.status === 409) {
      const limits = {
        too_many_drafts: "This file has too many drafts. Delete a draft and try again.",
        too_many_comments: "This file cannot hold more comments. Remove a new comment and try again.",
        too_many_replies: "This comment cannot hold more replies. Remove a reply and try again.",
      };
      if (typeof e.code === "string" && Object.hasOwn(limits, e.code)) next = limits[e.code];
    }
    return `${action} ${next}`;
  }

  async function call(method, path, payload, keepalive = false) {
    return (await exchange(method, path, payload, keepalive)).data;
  }

  // As call(), with the answer's headers too.
  async function exchange(method, path, payload, keepalive = false) {
    const init = { method, headers: { ...asAuthor }, cache: "no-store" };
    if (keepalive) init.keepalive = true;
    if (payload !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(payload);
    }
    let response;
    try {
      response = await fetch(`/a/${artifact}${path}`, init);
    } catch {
      throw new ApiError(0, "network");
    }
    if (response.status === 204) return { data: null, headers: response.headers };
    let data = null;
    try { data = await response.json(); } catch { /* not JSON, e.g. a proxy's page */ }
    if (response.ok && data !== null) return { data, headers: response.headers };
    const nameRejected = response.status === 400 && data?.error === "bad_request"
      && refusedNames.has(data.message);
    throw new ApiError(response.status, data?.error, nameRejected);
  }

  // The one way the comments are fetched: by load() and by poll(). The
  // header tells when the file was last uploaded.
  async function fetchComments() {
    const { data, headers } = await exchange("GET", "/comments");
    noteFile(headers.get("X-Artifact-Updated"));
    return data;
  }

  async function load() {
    const mine = ++generation;
    let found;
    try {
      found = await Promise.all([fetchComments(), call("GET", "/drafts")]);
    } catch (e) {
      if (mine === generation) toast(errorCopy(e, "Could not load comments."), { error: true, ms: 8000 });
      return;
    }
    if (mine !== generation) return; // something newer has landed meanwhile
    const [comments, drafts] = found;
    S.comments = comments;
    S.loaded = true;
    // Pending deletes and edits go by id, so they hold across this.
    S.drafts = drafts;
    // An item that is no longer there leaves the view.
    if (S.sel && !items().some((i) => i.key === S.sel)) unselect();
    render();
  }

  const changed = () => { generation++; };

  // --- Items -----------------------------------------------------------------
  // What to show is derived in comments-model.js; the browser's part (the
  // seen replies kept in storage, a pin's spot on the page) stays here.
  const seenKey = `aa-seen:${artifact}`;
  const seenPrefix = `${seenKey}:`;
  const seen = {};
  const validCount = (value) => Number.isSafeInteger(value) && value >= 0;
  function mergeSeen(n, count) {
    if (!/^\d+$/.test(n) || !validCount(count) || count <= (seen[n] || 0)) return false;
    seen[n] = count;
    return true;
  }
  // Acknowledgements are append-only by comment and count: concurrent tabs
  // cannot overwrite a newer count, even when acknowledging the same thread.
  function mergeSeenKey(key) {
    if (typeof key !== "string" || !key.startsWith(seenPrefix)) return false;
    const match = /^(\d+):(\d+)$/.exec(key.slice(seenPrefix.length));
    return match ? mergeSeen(match[1], Number(match[2])) : false;
  }
  try {
    const legacy = JSON.parse(localStorage.getItem(seenKey) || "{}");
    if (legacy && typeof legacy === "object" && !Array.isArray(legacy)) {
      for (const [n, count] of Object.entries(legacy)) mergeSeen(n, count);
    }
  } catch { /* unavailable or invalid legacy storage */ }
  try {
    for (let i = 0; i < localStorage.length; i++) {
      const key = localStorage.key(i);
      if (key && key.startsWith(seenPrefix) && localStorage.getItem(key) === "1") mergeSeenKey(key);
    }
  } catch { /* unavailable storage; keep what was read */ }
  const model = commentsModel(S, { pending, seen });
  const {
    liveDrafts, draftById, isComment, actsOn, decisionOn, repliesOn,
    reviewCount, items, itemOf, when, acceptable, acceptAllShown, groups, ordered, keptOrder, groupCounts,
  } = model;
  function markSeen(c) {
    if (!model.markSeen(c)) return;
    try { localStorage.setItem(`${seenPrefix}${c.n}:${seen[c.n]}`, "1"); } catch { /* kept for the page */ }
  }
  window.addEventListener("storage", (event) => {
    try { if (event.storageArea !== localStorage || event.newValue !== "1") return; }
    catch { return; }
    if (mergeSeenKey(event.key)) render();
  });

  // An item's pin is drawn when its anchor fits this content.
  const pinnable = (anchor) => Boolean(anchor && pinType && anchor.type === pinType);
  const lost = (it) => pinType === "element" && pinnable(it.anchor)
    && ((page.ready && page.missing.has(it.key)) || page.off);
  // Its element is on the page but not shown: another screen of an app, a
  // closed tab. Not an error, just not here now.
  const away = (it) => lost(it) && !page.off && page.hidden.has(it.key);
  const lostText = (it) => (!away(it) ? "spot not found"
    : it.anchor.screen ? `on “${short(it.anchor.screen, 32)}”` : "on another screen");
  const lostClass = (it) => (away(it) ? " away" : lost(it) && !page.off ? " lost" : "");

  // Where an item is, in a few words.
  function where(it) {
    if (!it.anchor) return "about the whole file";
    if (it.anchor.type === "point") return "on the image";
    return it.anchor.text ? `“${short(it.anchor.text, 24)}”` : "on the page";
  }
  const short = (s, n) => ([...s].length > n ? `${[...s].slice(0, n - 1).join("").trimEnd()}…` : s);

  // --- Small DOM helpers ---------------------------------------------------------
  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function button(label, onClick, className = "btn") {
    const b = el("button", className, label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  const ICONS = {
    send: "M12 19V5M6 11l6-6 6 6",
    left: "M15 5l-7 7 7 7",
    right: "M9 5l7 7-7 7",
    down: "M5 9l7 7 7-7",
  };
  // On a phone every view's title line ends with a fold button, at the
  // far right and far from Finish review at the bottom.
  function foldButton() {
    const b = button("", () => {
      if (desk.matches) { foldPanel(true); render(); }
      else fold(true);
    }, "chev fold");
    b.append(icon("down"));
    b.setAttribute("aria-label", desk.matches ? "Close comments" : "Fold the sheet");
    b.title = "Fold";
    return b;
  }
  function titled(text, extra = null) {
    const ttl = el("h2", "ttl", text);
    if (desk.matches && S.mode && !extra) {
      ttl.classList.add("handle");
      return ttl;
    }
    const head = el("div", "head handle");
    head.append(ttl);
    if (extra) head.append(extra);
    if (!desk.matches || !S.mode) head.append(foldButton());
    return head;
  }

  function icon(name) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("class", "icon");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", ICONS[name]);
    svg.append(path);
    return svg;
  }

  // The badge at a pin's corner, the same as pins.js draws in the page.
  const { mark, badge: badgeEl, inside } = pinMarks;

  function miniPin(it) {
    const tone = { own: " own", other: "", agent: " ag" }[it.tone] || "";
    const pin = el("span", `pin mini${tone}${it.fill !== "solid" ? ` ${it.fill}` : ""}`);
    // About the whole file: ≡ for a person's; the agent's keeps its star.
    pin.append(it.anchor || pinMarks.isStar(it.label) ? inside(it.label) : document.createTextNode("≡"));
    if (it.badge) pin.append(badgeEl(it.badge));
    pin.setAttribute("aria-hidden", "true");
    return pin;
  }

  // A field is one line while empty and grows with its text, up to its
  // style's max-height.
  function grow(area) {
    area.style.height = "auto";
    area.style.height = `${Math.max(area.scrollHeight, 40)}px`;
  }

  // The fields are made once and kept, so a redraw keeps what is typed.
  function field(placeholder, label, className = "") {
    const area = el("textarea", className);
    area.rows = 1;
    area.maxLength = 2000;
    area.placeholder = placeholder;
    area.setAttribute("aria-label", label);
    return area;
  }
  const fields = {
    compose: field("Comment", "Comment"),
    edit: field("Comment", "Edit the draft"),
    general: field("General comment", "General comment, optional"),
    reply: field("Reply…", "Reply"),
  };
  // The comment the reply field writes to.
  let replyFor = null;
  let savingDraft = false;
  let savingReply = false;
  const replyButton = button("", () => addReply(), "send");
  replyButton.append(icon("send"));
  replyButton.setAttribute("aria-label", "Reply");
  replyButton.title = "Reply";
  const sendButton = button("", () => addDraft(), "send");
  sendButton.append(icon("send"));
  sendButton.setAttribute("aria-label", "Add");
  sendButton.title = "Add";

  // --- Toast -------------------------------------------------------------------
  // One for the page: the viewer's menu (viewer.js) shows its messages here
  // too. With `undo` it has an Undo button.
  let undoAction = null;
  let toastTimer = null;
  function toast(message, { undo = null, ms = 2600, error = false } = {}) {
    clearTimeout(toastTimer);
    toastText.textContent = message;
    toastBox.classList.toggle("error", error);
    undoAction = undo;
    toastUndo.hidden = !undo;
    toastBox.classList.add("on");
    dockToast();
    toastTimer = setTimeout(hideToast, undo ? Math.max(ms, UNDO_MS) : ms);
  }

  function hideToast() {
    clearTimeout(toastTimer);
    toastBox.classList.remove("on");
    undoAction = null;
    toastUndo.hidden = true;
  }

  toastUndo.addEventListener("click", () => {
    const action = undoAction;
    hideToast();
    if (action) action();
  });

  document.addEventListener("aa:toast", (e) => {
    const { message, error = false } = e.detail || {};
    if (typeof message === "string") toast(message, { error, ms: error ? 10000 : 2500 });
  });

  // On a phone right above the sheet, or under the header when the sheet
  // takes the whole height; beside the panel the style sheet places it.
  function dockToast() {
    toastBox.style.top = "";
    toastBox.style.bottom = "";
    if (desk.matches) return;
    if (!sheet.hidden && S.pos === "f") {
      const header = document.querySelector("body > header");
      let top = header && header.getClientRects().length ? header.getBoundingClientRect().bottom : 0;
      // In full screen, under the floating menu button when it is up there.
      const float = $("float-button");
      if (float && !float.hidden && float.getBoundingClientRect().top < 100) {
        top = Math.max(top, float.getBoundingClientRect().bottom);
      }
      toastBox.style.top = `${top + 10}px`;
      toastBox.style.bottom = "auto";
      return;
    }
    const top = sheet.hidden ? window.innerHeight : sheet.getBoundingClientRect().top;
    toastBox.style.bottom = `${Math.max(0, window.innerHeight - top) + 8}px`;
  }

  // The list's groups that start folded: nothing in them waits for the person.
  const FOLDED = new Set(["talk", "closed"]);

  // --- Drawing -------------------------------------------------------------------
  function render() {
    // A field with the focus is put back with its caret after the redraw,
    // and a group's title line with the focus gets it back.
    const active = document.activeElement;
    const kept = Object.values(fields).includes(active) ? active : null;
    const keptGroup = active && active.classList.contains("sec-fold") ? active.dataset.group : null;
    const keptHeading = active && active.matches(".sbody > .handle");
    const caret = kept ? [kept.selectionStart, kept.selectionEnd] : null;

    const shown = (desktopPanel() && !document.body.classList.contains("comments-folded")) || S.mode || S.compose || S.comments.length > 0 || liveDrafts().length > 0 || (!S.loaded && Number(sheet.dataset.count) > 0);
    sheet.hidden = !shown;
    if (!shown) S.pos = "strip";
    sheet.dataset.pos = desk.matches ? "panel" : S.pos;
    document.body.classList.toggle("commenting", S.mode);
    toggle.setAttribute("aria-pressed", String(S.mode));
    toggle.title = S.mode ? "Stop commenting" : "Comment";

    renderStrip();
    // On a phone the strip alone shows nothing else.
    const open = desk.matches || S.pos !== "strip";
    if (S.view === "compose" && !S.compose) S.view = "list";
    // Off one item, ‹ › take the list's order afresh next time.
    if (S.view !== "item") S.order = null;
    // Folded, the list starts again with its groups folded.
    if (!desk.matches && S.pos === "strip") S.opened.clear();
    const [content, footer] = open ? views[S.view]() : [[], []];
    body.replaceChildren(...content);
    foot.replaceChildren(...footer);
    for (const area of body.querySelectorAll("textarea")) grow(area);
    for (const area of foot.querySelectorAll("textarea")) grow(area);
    if (kept && kept.isConnected) {
      kept.focus({ preventScroll: true });
      kept.setSelectionRange(...caret);
    }
    if (keptHeading) {
      const heading = body.querySelector(".handle");
      if (heading) { heading.tabIndex = -1; heading.focus({ preventScroll: true }); }
    }
    if (keptGroup) {
      const head = body.querySelector(`.sec-fold[data-group="${keptGroup}"]`);
      if (head) head.focus({ preventScroll: true });
    }
    // A field being typed in hides what would sit under the finger (the
    // item's Finish review, the choice of where to send).
    const typing = document.activeElement === fields.reply || document.activeElement === fields.general;
    foot.classList.toggle("typing", typing);
    drawPins();
    syncPicking();
    dockToast();
    // The floating menu button of full screen (viewer.js) keeps off the sheet.
    document.dispatchEvent(new CustomEvent("aa:sheet"));
  }

  let pointerKind = matchMedia("(pointer: coarse)").matches ? "touch" : "mouse";
  const pinHint = () => `${pointerKind === "touch" ? "Tap" : "Click"} a spot to comment.`;
  document.addEventListener("pointerdown", updatePointer, true);
  document.addEventListener("pointermove", updatePointer, true);
  function updatePointer(e) {
    const kind = e.pointerType === "touch" ? "touch" : "mouse";
    if (kind === pointerKind) return;
    pointerKind = kind;
    for (const node of [stripText, ...strip.querySelectorAll(".strip-hint"), ...body.querySelectorAll(".empty")]) {
      if (node.textContent === "Tap a spot to comment." || node.textContent === "Click a spot to comment.") {
        node.textContent = pinHint();
      }
    }
  }

  // The strip: what waits for the person, in bold, then their drafts and
  // what the agent has; words only, the pins are in the list.
  function renderStrip() {
    const pendingCount = !S.loaded ? Number(sheet.dataset.count) : 0;
    const review = reviewCount();
    const n = groupCounts();
    const attention = desk.matches && n.need > 0;
    toggle.classList.toggle("needs-attention", attention);
    toggle.setAttribute("aria-label", attention ? "Comment mode, needs your attention" : "Comment mode");
    const menuComment = document.querySelector('[data-act="comment"]');
    if (menuComment) {
      menuComment.classList.toggle("needs-attention", attention);
      if (attention) menuComment.setAttribute("aria-describedby", "comment-attention");
      else menuComment.removeAttribute("aria-describedby");
    }
    const more = desk.matches || review ? "" : " ›";
    const many = (k) => `${k} ${k === 1 ? "comment" : "comments"}`;
    const parts = [];
    if (pendingCount) parts.push(many(pendingCount));
    if (n.need) parts.push(el("b", null, `${n.need} need you`));
    if (review) parts.push(`${review} ${review === 1 ? "draft" : "drafts"}`);
    // With Finish review at its side the line has no room for the agent's part.
    if (n.agent && !review) parts.push(`${n.agent} in progress`);
    const line = [];
    parts.forEach((p, i) => line.push(...(i ? [" · ", p] : [p])));
    const plain = !parts.length;
    if (!plain) line.push(more);
    else if (S.comments.length) line.push(`${many(S.comments.length)}${more}`);
    else if (S.mode && canPin()) line.push(pinHint());
    else line.push("No comments yet");
    stripText.replaceChildren(...line);
    for (const child of [...strip.children]) if (child !== stripText) child.remove();
    // In comment mode, with comments to list, the hint goes to the right.
    if (plain && S.comments.length && S.mode && canPin()) {
      strip.append(el("span", "strip-hint", pinHint()));
    }
    if (review && S.view !== "finish") {
      strip.append(button("Finish review", (e) => { e.stopPropagation(); openFinish(); }, "btn pri"));
    }
  }

  const views = {
    list() {
      const pending = !S.loaded && Number(sheet.dataset.count) > 0;
      const review = reviewCount();
      const all = items();
      const drafts = all.filter((i) => i.draft).length;
      const out = [titled(pending ? "Loading comments…" : all.length ? `Comments · ${all.length}` : "No comments yet")];
      if (!pending && !all.length) {
        out.push(el("p", "empty", S.mode && canPin()
          ? pinHint()
          : S.mode ? "Nobody has commented yet." : "Turn on the speech bubble to comment."));
      }
      for (const g of groups()) {
        const head = `${g.title} · ${g.items.length}`;
        if (FOLDED.has(g.id)) {
          // Shown open while one of its items is selected, so its row lights up.
          const open = S.opened.has(g.id) || g.items.some((i) => i.key === S.sel);
          const b = button("", () => {
            if (open) {
              S.opened.delete(g.id);
              if (g.items.some((i) => i.key === S.sel)) S.sel = null;
            } else S.opened.add(g.id);
            render();
          }, "sec sec-fold");
          b.dataset.group = g.id;
          b.append(el("span", null, head), icon(open ? "down" : "right"));
          b.setAttribute("aria-expanded", String(open));
          out.push(b);
          if (!open) continue;
        } else if (g.id === "need") {
          const sec = el("div", "sec-row");
          sec.append(el("p", "sec", head));
          const accept = acceptAllButton();
          if (accept) sec.append(accept);
          out.push(sec);
        } else out.push(el("p", "sec", head));
        for (const it of g.items) out.push(row(it, status(it)));
      }
      if (S.mode) {
        const add = el("div", "addwhole");
        add.append(button("+ About the whole file", () => composeWhole()));
        out.push(add);
      }
      if (!stored && (S.mode || drafts)) out.push(el("p", "note", "This browser keeps nothing: your drafts last only while this page is open."));
      // On a computer Finish review is in the strip's line heading the panel.
      return [out, review && !desk.matches ? [finishBar(review)] : []];
    },

    item() {
      const it = itemOf(S.sel);
      if (!it) return views.list();
      // ‹ › and the rows under it go in the list's order, as it was.
      const all = inOrder();
      const at = all.findIndex((i) => i.key === it.key);
      const nav = el("div", "nav handle");
      nav.append(
        button("‹ All", () => go("list"), "btn all"),
        button(`${at + 1} of ${all.length} · ${where(it)}`, () => go("list", "m"), "lbl"),
      );
      const prev = button("", () => step(-1), "chev");
      prev.append(icon("left"));
      prev.setAttribute("aria-label", "Previous");
      const next = button("", () => step(1), "chev");
      next.append(icon("right"));
      next.setAttribute("aria-label", "Next");
      nav.append(prev, next);
      if (!desk.matches || !S.mode) nav.append(foldButton());

      const out = [nav, ...(it.draft ? [draftMsg(it)] : thread(it))];
      // Only full height includes the other items.
      if (!desk.matches && S.pos === "f") {
        const others = all.filter((o) => o.key !== it.key);
        if (others.length) {
          const heading = el("p", "sec full-only", "Others");
          out.push(heading);
        }
        for (const o of others) {
          const other = row(o, status(o));
          other.classList.add("full-only");
          out.push(other);
        }
      }
      // The strip is hidden while the sheet is open: Finish review is here,
      // as in the list (on a computer it heads the panel).
      const review = reviewCount();
      const footer = [];
      const accepting = !it.draft && (decisionOn(it.c.n) || {}).kind === "accept";
      if (!it.draft && !it.c.rejected_by && !accepting && !S.edit) footer.push(replyPill(it));
      if (review && !S.edit && !desk.matches) footer.push(finishBar(review));
      return [out, footer];
    },

    compose() {
      const c = S.compose;
      const out = [el("p", "cap handle", c.anchor ? "New draft" : "New draft · about the whole file")];
      if (!stored) out.push(el("p", "note", "This browser keeps nothing: your drafts last only while this page is open."));
      const pill = el("div", "pill");
      pill.append(fields.compose, sendButton);
      sendButton.disabled = savingDraft || !fields.compose.value.trim();
      // On a phone the field is the sheet's footer, so it stays at the
      // bottom edge, right above the keyboard.
      return [out, [pill]];
    },

    finish() {
      const drafts = items().filter((i) => i.draft);
      // One row per comment: a new one, or one decided or replied to.
      const touched = items().filter((i) => i.c && actsOn(i.c.n).length);
      const count = drafts.length + touched.length;
      // What waits for the person's yes and is not decided yet comes first,
      // so none is left behind: the agent's answers, and colleagues'
      // comments awaiting approval. Then what this review changes, what is
      // accepted last.
      const undecided = items().filter((i) => i.c && !actsOn(i.c.n).length && (i.c.by_agent
        ? i.c.agent === "asked" || model.proposalCanApprove(i.c)
        : ["done", "wrote", "asked"].includes(i.c.agent) || i.pend));
      const accepting = (i) => (decisionOn(i.c.n) || {}).kind === "accept";
      const out = [titled(`Finish review · ${count}`)];
      if (undecided.length) {
        const sec = el("div", "sec-row");
        sec.append(el("p", "sec", `Not decided yet · ${undecided.length}`));
        const all = acceptAllButton("link");
        if (all) sec.append(all);
        out.push(sec);
        for (const it of undecided) out.push(row(it, status(it)));
        out.push(el("p", "sec", "In this review"));
      }
      if (!count) out.push(el("p", "empty", "No drafts: you can publish a general comment only."));
      for (const it of drafts) out.push(row(it, where(it)));
      for (const it of touched.filter((i) => !accepting(i))) out.push(row(it, status(it)));
      for (const it of touched.filter(accepting)) out.push(row(it, status(it)));
      const general = el("div", "general");
      general.append(fields.general);
      out.push(general);
      const bar = el("div", "foot2");
      const done = button("Finish review", () => finish(), "btn pri big");
      finishButton = done;
      done.disabled = sending || (!count && !fields.general.value.trim());
      bar.append(button("Back", () => go("list", "m")), done);
      // The choice stays in sight next to the button it changes.
      return [out, [toWhom(), bar]];
    },
  };

  // Finish review on the finish view, enabled by a general comment.
  let finishButton = null;
  // A review on its way: no second one, and the choice holds.
  let sending = false;

  // Where the review goes: to the agent (the default) or only published.
  function toWhom() {
    const box = el("div", "towhom");
    const seg = el("div", "seg");
    seg.setAttribute("role", "radiogroup");
    seg.setAttribute("aria-label", "Where the comments go");
    const show = () => {
      for (const b of seg.children) b.setAttribute("aria-checked", String(b.agent === S.toAgent));
    };
    for (const [agent, label] of [[true, "Send to agent"], [false, "Only publish"]]) {
      // Changed in place, not by render(), so focus stays on the option.
      const b = button(label, () => {
        if (sending) return;
        S.toAgent = agent;
        show();
      }, "opt");
      b.agent = agent;
      b.setAttribute("role", "radio");
      seg.append(b);
    }
    show();
    box.append(seg);
    return box;
  }

  // A new comment's draft, alone in the item view.
  function draftMsg(it) {
    const msg = el("div", "msg unsent");
    const bd = el("div", "bd");
    const who = el("span", "who", "Your draft");
    who.append(el("span", "when", when(it.d.updated_at)));
    bd.append(who);
    if (S.edit === it.d.id) bd.append(...editor(it.d));
    else {
      bd.append(el("div", "text", it.text));
      if (lost(it)) bd.append(el("span", `st${lostClass(it)}`, page.off ? `on ${where(it)}` : `${lostText(it)} · ${where(it)}`));
      const acts = el("div", "acts");
      acts.append(button("Edit", () => startEdit(it.d)), button("Delete", () => deleteDraft(it.d)));
      bd.append(acts);
    }
    msg.append(miniPin(it), bd);
    return msg;
  }

  function editor(d) {
    const acts = el("div", "acts");
    acts.append(button("Save", () => saveEdit(d), "btn pri"), button("Cancel", () => {
      S.edit = null;
      render();
    }));
    return [fields.edit, acts];
  }

  // A published comment as a small chat: the comment, its replies, the
  // person's decision and unsent replies, and what can be done next.
  function thread(it) {
    const c = it.c;
    const out = [];
    const head = el("div", "msg");
    const bd = el("div", "bd");
    const who = el("span", "who", c.by_agent ? "Agent" : c.author);
    who.append(el("span", "when", when(c.created_at)));
    bd.append(who, el("div", "text", c.text));
    const notes = [];
    // With the agent, the replaced file is its answer, not news.
    if (c.before_replace && !c.agent) notes.push("written before the file was replaced");
    if (lost(it)) notes.push(page.off ? `on ${where(it)}` : `${lostText(it)} · ${where(it)}`);
    if (notes.length) bd.append(el("span", `st${lostClass(it)}`, notes.join(" · ")));
    if (c.approved_by) bd.append(el("span", "ref", `✓ Approved by ${c.approved_by}`));
    else if (c.rejected_by) bd.append(el("span", "ref", `✕ Rejected by ${c.rejected_by}`));
    else if (model.proposalCanApprove(c)) bd.append(el("span", "ref", "Awaiting approval"));
    else if (it.pend) bd.append(el("span", "ref", "Awaiting approval · not sent to the agent"));
    head.append(miniPin(it), bd);
    out.push(head);
    for (const r of c.replies) {
      const m = el("div", `msg${!r.mine && !r.agent ? " colleague-reply" : ""}`);
      const rb = el("div", "bd");
      const rw = el("span", "who", r.agent ? "Agent" : r.mine ? "You" : r.author);
      rw.append(el("span", "when", when(r.created_at)));
      rb.append(rw, el("div", "text", r.text));
      const av = r.agent ? el("span", "ai") : el("span", `av${r.mine ? " own" : ""}`, model.initial(r.author));
      if (r.agent) av.append(mark("star"));
      m.append(av, rb);
      out.push(m);
    }
    const dec = decisionOn(c.n);
    if (dec) {
      const line = el("div", "dec");
      const said = { approve: ["✓ Accepted", "goes with your review"], reject: ["✕ Rejected", "goes with your review"],
        accept: ["✓ Accepted", "closes with your review"] }[dec.kind];
      line.append(el("b", dec.kind === "reject" ? "no" : "", said[0]), el("span", "", said[1]),
        button("Undo", () => undoDecision(dec), "undo"));
      out.push(line);
    }
    for (const d of repliesOn(c.n)) {
      const m = el("div", "msg unsent");
      const rb = el("div", "bd");
      const rw = el("span", "who", "Your reply");
      rw.append(el("span", "when", when(d.updated_at)));
      rb.append(rw);
      if (S.edit === d.id) rb.append(...editor(d));
      else {
        rb.append(el("div", "text", d.text));
        const acts = el("div", "acts");
        acts.append(button("Edit", () => startEdit(d)), button("Delete", () => deleteDraft(d)));
        rb.append(acts);
      }
      const av = el("span", "av own", "↩");
      m.append(av, rb);
      out.push(m);
    }
    // Buttons only on the selected comment, and only what fits its state.
    if (!dec && !c.rejected_by) {
      const acts = el("div", "acts thread-acts");
      if (model.proposalCanApprove(c)) {
        acts.append(button("✓ Accept", () => decide(c, "approve"), "btn pri"));
      } else if (!c.by_agent && it.pend) {
        acts.append(button("✓ Accept", () => decide(c, "approve"), "btn pri"),
          button("✕ Reject", () => decide(c, "reject")));
      } else if (!c.by_agent && (c.agent === "done" || c.agent === "wrote") && !repliesOn(c.n).length) {
        acts.append(button("✓ Accept", () => decide(c, "accept"), "btn pri"));
      }
      if (acts.children.length) out.push(acts);
    }
    return out;
  }

  function replyPill(it) {
    const c = it.c;
    fields.reply.placeholder = c.by_agent || c.agent ? "Reply to the agent…" : c.mine ? "Add a reply…" : `Reply to ${c.author}…`;
    fields.reply.setAttribute("aria-label", fields.reply.placeholder.replace("…", ""));
    replyFor = c.n;
    const pill = el("div", "pill");
    pill.append(fields.reply, replyButton);
    replyButton.disabled = savingReply || !fields.reply.value.trim();
    return pill;
  }

  // A row's second line, with where its pin is when that is not shown.
  function status(it) {
    return model.status(it, lost(it) && !page.off ? lostText(it) : null);
  }

  function row(it, line) {
    const r = el("button", `it${it.draft && S.view !== "finish" ? " dr" : ""}${S.sel === it.key && S.view === "list" ? " cur" : ""}`);
    r.type = "button";
    r.dataset.key = it.key;
    const tx = el("span", "tx");
    tx.append(el("span", "main", (it.anchor ? "" : "About the whole file: ") + it.text));
    const st = el("span", `st${lostClass(it)}`);
    const [lead, rest] = Array.isArray(line) ? line : [null, line];
    if (lead) st.append(lead[0] ? el("b", lead[0], lead[1]) : lead[1], " · ");
    st.append(rest);
    tx.append(st);
    r.append(miniPin(it), tx);
    r.addEventListener("click", () => openItem(it.key));
    return r;
  }

  function finishBar(count) {
    const bar = el("div", "foot2");
    bar.append(button(`Finish review · ${count}`, () => openFinish(), "btn pri"));
    return bar;
  }

  // --- Pins ----------------------------------------------------------------------
  // What is drawn: the listed items with a pin here, and the new draft's spot.
  function pinList() {
    const shown = items().filter((it) => pinnable(it.anchor)).map((it) => ({
      key: it.key, label: it.label, anchor: it.anchor, tone: it.tone, fill: it.fill, badge: it.badge,
      lit: S.sel === it.key && (S.view === "item"
        || (S.view === "list" && (desk.matches || S.pos !== "strip"))),
    }));
    if (S.compose && pinnable(S.compose.anchor)) {
      shown.push({ key: "new", label: "", anchor: S.compose.anchor, tone: "own", fill: "line", badge: "", lit: true });
    }
    return shown;
  }

  // A tap on a pin: with the list open, its row lights up and comes into
  // view, the sheet stays; otherwise it opens its item. On the new draft's
  // pin, its field.
  function tapPin(key) {
    const folded = desktopPanel() && document.body.classList.contains("comments-folded");
    if (key !== "new" && itemOf(key) && protectRetainedCompose()) return;
    if (folded) foldPanel(false);
    if (key === "new") {
      if (S.compose) {
        if (S.view !== "compose" || S.pos === "strip") go("compose", "m");
        fields.compose.focus({ preventScroll: true });
      }
      return;
    }
    if (!itemOf(key)) return;
    if (folded || (desktopPanel() && !S.mode)) {
      const fromFrame = frame && document.activeElement === frame;
      if (openItem(key) && fromFrame && desk.matches && !S.mode) {
        body.querySelector('.fold[aria-label="Close comments"]')?.focus({ preventScroll: true });
      }
      return;
    }
    if (S.view === "list" && (desk.matches || S.pos !== "strip")) {
      if (S.compose) dropCompose(false);
      S.sel = key;
      render();
      showRow(key);
    } else openItem(key);
  }

  function showRow(key) {
    const r = body.querySelector(`.it[data-key="${CSS.escape(key)}"]`);
    if (r) r.scrollIntoView({ block: "nearest" });
  }

  // Whether a typed reply or edit kept the comment when the pins were last
  // sent to the frame.
  let kept = false;
  // Drawn over the image or in the page's frame by comments-pins.js.
  // render(), canPin() and lost() read `page`, which exists only from this
  // line on: nothing above it may call render() while the script first runs.
  const { page, drawPins, fitPins, reveal, revealSoon, syncPicking } = commentsPins({
    pinType, image, imageBox, imageLink: $("image-link"), pinLayer, frame,
    pins: pinList,
    picking: () => S.mode && dialog.hidden,
    onPin: tapPin,
    onSpot: place,
    onEscape: escape,
    onBlank: blank,
    // What the frame was last told is kept, see keptChanged.
    hold: () => { kept = keeps(); return !kept; },
    // The list and the item say which spots were not found; a field being
    // typed in is not redrawn for that.
    onChange: (spotsOnly) => {
      if (!spotsOnly || !Object.values(fields).includes(document.activeElement)) render();
    },
  });

  // Settings (menu-dialogs.js) changed how pins look: drawn again, and the
  // page's frame told.
  document.addEventListener("aa:look", () => drawPins());

  // --- Navigation ------------------------------------------------------------------
  // Every view opens at its intrinsic middle height.
  const DEFAULT_POS = { list: "m", item: "m", compose: "m", finish: "m" };

  function go(view, pos) {
    S.view = view;
    if (!desk.matches) S.pos = pos || DEFAULT_POS[view];
    if (view !== "item") S.edit = null;
    render();
    // Back on the list, the lit row is brought into view.
    if (view === "list" && S.sel) requestAnimationFrame(() => showRow(S.sel));
  }

  function unselect() {
    S.sel = null;
    S.edit = null;
    if (S.view === "item") S.view = "list";
  }

  // A composer retained when desktop full-screen placement was turned off
  // stays protected after leaving full screen or changing the viewport.
  function protectRetainedCompose() {
    if (!S.compose?.retained || !fields.compose.value) return false;
    if (desktopPanel() && document.body.classList.contains("comments-folded")) foldPanel(false);
    if (S.view !== "compose" || (!desk.matches && S.pos === "strip")) go("compose", "m");
    fields.compose.focus({ preventScroll: true });
    toast("Save or discard the draft first");
    return true;
  }

  function openItem(key) {
    if (protectRetainedCompose()) return;
    // An edit with typed changes is not dropped by moving to another item.
    if (S.sel !== key && fields.reply.value.trim()) {
      toast("Send or clear the reply first");
      return;
    }
    if (S.edit && S.sel !== key) {
      const d = draftById(S.edit);
      if (d && fields.edit.value !== d.text) {
        toast("Save or cancel the edit first");
        return;
      }
    }
    if (S.compose) dropCompose(false);
    if (S.sel !== key) {
      S.edit = null;
      fields.reply.value = "";
    }
    S.sel = key;
    // Entering one item: the list's order is kept while stepping.
    if (S.view !== "item" || !S.order) S.order = ordered().map((i) => i.key);
    const opened = itemOf(key);
    if (document.visibilityState === "visible" && opened && opened.c) markSeen(opened.c);
    // One comment: the sheet fits it. Pulled up with the others under it,
    // it keeps its height and the chosen one comes to the top.
    const keep = S.view === "item" && (S.pos === "f");
    go("item", keep ? S.pos : "m");
    if (keep) body.scrollTop = 0;
    else toChatEnd();
    revealSoon(key);
    return true;
  }

  // At middle height a comment's chat shows its end: the message being answered sits
  // right above the reply field.
  function toChatEnd() {
    const it = itemOf(S.sel);
    if (!desk.matches && S.view === "item" && S.pos === "m" && it && !it.draft) body.scrollTop = body.scrollHeight;
  }

  // The items in the order kept for one item, or the list's.
  const inOrder = () => (S.order ? keptOrder(S.order) : ordered());

  function step(by) {
    const all = inOrder();
    if (!all.length) return;
    const at = all.findIndex((i) => i.key === S.sel);
    openItem(all[(at + by + all.length) % all.length].key);
  }

  // Back to the strip on a phone, to the list on a computer.
  function fold(explicit = false) {
    // A typed reply would be left without its comment, and the next item
    // would refuse to open: it is sent or cleared first.
    const editing = S.edit && draftById(S.edit);
    const dirty = editing && fields.edit.value !== editing.text;
    if (fields.reply.value.trim() || dirty) {
      toast(dirty ? "Save or cancel the edit first" : "Send or clear the reply first");
      render();
      return;
    }
    const restoreFocus = sheet.contains(document.activeElement);
    S.sel = null;
    S.edit = null;
    S.view = "list";
    S.pos = "strip";
    render();
    if ((explicit || restoreFocus) && !desk.matches) focusFoldTarget();
  }

  function focusFoldTarget() {
    const target = stripText.getClientRects().length ? stripText : $("float-button");
    if (target && target.getClientRects().length) target.focus({ preventScroll: true });
  }

  // A typed reply or a changed edit keeps the selected comment.
  function keeps() {
    const editing = S.edit && draftById(S.edit);
    return Boolean(fields.reply.value.trim() || (editing && fields.edit.value !== editing.text));
  }

  // A tap on the content, outside comment mode, lets go of the selected
  // comment, as the fold button does. With nothing selected, or with a
  // reply typed or an edit changed, it does nothing, quietly: the person
  // may only be reading the page, and the page then gets its taps (the
  // frame is told not to hold them).
  function blank() {
    if (S.mode || !S.sel || keeps()) return;
    fold();
  }

  // --- Comment mode and the name ---------------------------------------------------
  let afterName = null;
  function askName(then) {
    afterName = then;
    dialog.hidden = false;
    syncPicking();
    nameInput.focus();
  }

  function closeName(value) {
    dialog.hidden = true;
    const then = afterName;
    afterName = null;
    if (value === null) {
      const target = finishButton && finishButton.isConnected && !finishButton.disabled
        ? finishButton : body.querySelector(".handle") || stripText;
      if (!target.hasAttribute("tabindex") && target !== finishButton) target.tabIndex = -1;
      target.focus({ preventScroll: true });
      syncPicking();
      return;
    }
    name = cleanName(value);
    storage.set(NAME_KEY, name);
    syncPicking();
    if (then) then();
  }

  $("name-form").addEventListener("submit", (e) => {
    e.preventDefault();
    closeName(nameInput.value);
  });
  $("name-skip").addEventListener("click", () => closeName(""));
  // The name changed in Settings (menu-dialogs.js): for the next drafts.
  document.addEventListener("aa:name", (e) => {
    const value = e.detail && e.detail.name;
    if (typeof value !== "string") return;
    name = cleanName(value);
    storage.set(NAME_KEY, name);
  });
  dialog.addEventListener("click", (e) => { if (e.target === dialog) closeName(null); });

  function setMode(on, then = null) {
    if (!on && desktopPanel() && S.compose) S.compose.retained = true;
    S.mode = on;
    if (desktopPanel()) foldPanel(!on);
    if (!on && !desktopPanel() && !S.compose?.retained) {
      if (S.compose) dropCompose(false);
      const it = itemOf(S.sel);
      if (!it || S.view === "finish") {
        S.sel = null;
        S.edit = null;
        S.view = "list";
        if (S.pos !== "m") S.pos = "strip";
      }
    } else if (on && !desk.matches && S.compose) {
      S.view = "compose";
      if (S.pos === "strip") S.pos = "m";
    } else if (on && !canPin() && !desk.matches && S.pos === "strip") {
      // Nothing to tap on: the list, with "About the whole file".
      S.view = "list";
      S.pos = "m";
    }
    render();
    if (then) then();
  }

  toggle.addEventListener("click", () => setMode(!S.mode));

  // --- A new draft -----------------------------------------------------------------
  function place(anchor) {
    if (!S.mode) return;
    if (S.compose && S.compose.anchor) {
      // Moving the spot keeps what is typed.
      S.compose.anchor = anchor;
      if (S.view !== "compose" || S.pos === "strip") go("compose", "m");
      else drawPins();
    } else {
      if (S.compose && fields.compose.value && (S.compose.retained || document.body.classList.contains("full"))) {
        go("compose", "m");
        toast("Save or discard the draft first");
        return;
      }
      if (S.compose) dropCompose(true);
      S.compose = { anchor };
      S.sel = null;
      go("compose", "m");
    }
    fields.compose.focus({ preventScroll: true });
    revealSoon("new");
  }

  function composeWhole() {
    if (S.compose && fields.compose.value && (S.compose.retained || document.body.classList.contains("full"))) {
      go("compose", "m");
      fields.compose.focus({ preventScroll: true });
      return;
    }
    if (S.compose) dropCompose(true);
    S.compose = { anchor: null };
    S.sel = null;
    go("compose", "m");
    fields.compose.focus({ preventScroll: true });
  }

  // Drops the new draft; typed text comes back with Undo unless `quiet`.
  function dropCompose(quiet) {
    const kept = { compose: S.compose, text: fields.compose.value };
    S.compose = null;
    fields.compose.value = "";
    if (quiet || (!kept.text.trim() && !(kept.compose.retained && kept.text))) return;
    const restore = () => {
      if (!kept.compose.retained && !S.mode) return;
      const editing = S.edit && draftById(S.edit);
      if (kept.compose.retained && ((S.compose && fields.compose.value) || fields.reply.value ||
          (editing && fields.edit.value !== editing.text) || fields.general.value)) {
        toast("Save or discard your changes first", { undo: restore });
        return;
      }
      S.compose = kept.compose;
      fields.compose.value = kept.text;
      if (desktopPanel() && document.body.classList.contains("comments-folded")) foldPanel(false);
      go("compose", "m");
      fields.compose.focus({ preventScroll: true });
    };
    toast("Draft discarded", { undo: restore });
  }

  // The server refused the name (one kept from an older version, say): it
  // is forgotten and asked again; the text stays where it is.
  function nameRefused(e) {
    if (!e.nameRejected) return false;
    nameInput.value = name || nameInput.value;
    name = null;
    try { localStorage.removeItem(NAME_KEY); } catch { /* not kept anyway */ }
    toast("That name cannot be used. Give another one.", { error: true, ms: 6000 });
    return true;
  }

  async function addDraft() {
    const c = S.compose;
    const text = fields.compose.value;
    if (!c || !text.trim() || savingDraft) return false;
    savingDraft = true;
    sendButton.disabled = true;
    try {
      const draft = await call("POST", "/drafts", { author: name, text, anchor: c.anchor });
      changed();
      S.drafts.push(draft);
      if (S.compose === c && fields.compose.value !== text) {
        render();
        return true;
      }
      if (S.compose === c) {
        S.compose = null;
        fields.compose.value = "";
      }
      S.view = "list";
      S.pos = "strip";
      render();
      strip.classList.remove("pulse");
      void strip.offsetWidth;
      strip.classList.add("pulse");
      if (!c.anchor) toast("Added about the whole file");
      return true;
    } catch (e) {
      if (!nameRefused(e)) toast(errorCopy(e, "Could not save the draft."), { error: true, ms: 8000 });
      return false;
    } finally {
      savingDraft = false;
      sendButton.disabled = savingDraft || !fields.compose.value.trim();
    }
  }

  // --- Decisions and replies on a published comment ----------------------------------
  // A decision replaces the person's earlier one on the comment (the server
  // does the same); Undo deletes it at once.
  let deciding = false;
  async function decide(c, kind) {
    if (deciding) return;
    deciding = true;
    try {
      const draft = await call("POST", "/drafts", { author: name, kind, of: c.n });
      changed();
      S.drafts = S.drafts.filter((d) => isComment(d) || d.of !== c.n || d.kind === "reply");
      S.drafts.push(draft);
      render();
    } catch (e) {
      if (!nameRefused(e)) toast(errorCopy(e, "Could not save the decision."), { error: true, ms: 8000 });
    } finally {
      deciding = false;
    }
  }

  // Small enough to share a title line: a pill there, a link by a section.
  function acceptAllButton(look = "pill") {
    if (!acceptAllShown()) return null;
    const n = acceptable().length;
    const b = button(look === "pill" ? `✓ All · ${n}` : `Accept all · ${n}`, () => acceptAll(),
      look === "pill" ? "all-pill" : "all-link");
    b.setAttribute("aria-label", `Accept all ${n} waiting`);
    return b;
  }
  // Each becomes an acceptance draft, as if accepted one by one, in one call:
  // the server makes all of them or none. One Undo takes them all back.
  async function acceptAll() {
    if (deciding || sending) return;
    const decisions = acceptable().map(([c, kind]) => ({ kind, of: c.n }));
    if (!decisions.length) return;
    deciding = true;
    // A load already on its way would bring the drafts from before.
    changed();
    let made;
    try {
      made = await call("POST", "/drafts/batch", { author: name, decisions });
    } catch (e) {
      if (!nameRefused(e)) toast(errorCopy(e, "Nothing accepted."), { error: true, ms: 8000 });
      return;
    } finally {
      deciding = false;
    }
    changed();
    S.drafts.push(...made);
    render();
    const approved = made.filter((d) => d.kind === "approve").length;
    const accepted = made.length - approved;
    toast([accepted && `Accepted ${accepted}`, approved && `approved ${approved}`].filter(Boolean).join(", ")
      .replace(/^a/, "A"), { undo: () => undoAll(made) });
  }

  // Deletes the drafts Accept all made, in one call; one already gone (a
  // review sent meanwhile, say) is skipped by the server.
  async function undoAll(made) {
    if (deciding || sending) {
      toast("Still saving. Try again.", { undo: () => undoAll(made) });
      return;
    }
    const ids = new Set(made.map((d) => d.id));
    deciding = true;
    changed();
    try {
      await call("POST", "/drafts/delete", { ids: [...ids] });
    } catch (e) {
      toast(errorCopy(e, "Not undone."), { error: true, ms: 8000 });
      return;
    } finally {
      deciding = false;
    }
    changed();
    S.drafts = S.drafts.filter((x) => !ids.has(x.id));
    render();
  }

  async function undoDecision(d) {
    try {
      await call("DELETE", `/drafts/${d.id}`);
    } catch (e) {
      if (e.status !== 404) {
        toast(errorCopy(e, "Could not undo the decision."), { error: true, ms: 8000 });
        return;
      }
    }
    changed();
    S.drafts = S.drafts.filter((x) => x.id !== d.id);
    render();
  }

  async function addReply() {
    const n = replyFor;
    const text = fields.reply.value;
    if (n === null || !text.trim() || savingReply) return;
    savingReply = true;
    replyButton.disabled = true;
    try {
      const draft = await call("POST", "/drafts", { author: name, kind: "reply", of: n, text });
      changed();
      S.drafts.push(draft);
      if (replyFor === n && fields.reply.value === text) fields.reply.value = "";
      // Replying means "not yet": an acceptance of the same comment goes.
      const dec = decisionOn(n);
      if (dec && dec.kind === "accept") await undoDecision(dec);
      // Sent: the keyboard goes and Finish review comes back, the usual next
      // step; a tap on the field writes another reply.
      fields.reply.blur();
      render();
      toChatEnd();
    } catch (e) {
      if (!nameRefused(e)) toast(errorCopy(e, "Could not save the reply."), { error: true, ms: 8000 });
    } finally {
      savingReply = false;
      replyButton.disabled = savingReply || !fields.reply.value.trim();
    }
  }

  // --- Editing and deleting a draft -------------------------------------------------
  function startEdit(d) {
    S.edit = d.id;
    fields.edit.value = d.text;
    render();
    fields.edit.focus();
    const end = fields.edit.value.length;
    fields.edit.setSelectionRange(end, end);
  }

  async function saveEdit(d) {
    const id = d.id;
    const text = fields.edit.value;
    if (!text.trim()) {
      toast("Write something, or delete the draft.", { error: true });
      return;
    }
    if (text === d.text) {
      S.edit = null;
      render();
      return;
    }
    try {
      const saved = await call("PATCH", `/drafts/${id}`, { text });
      changed();
      const now = draftById(id);
      if (now) Object.assign(now, saved);
      if (S.edit === id) S.edit = null;
      render();
      toast("Saved");
    } catch (e) {
      toast(errorCopy(e, "Could not save the draft."), { error: true, ms: 8000 });
    }
  }

  // Hidden at once; the DELETE goes when the Undo time ends, so Undo keeps
  // the draft's id and its place. All by id: a reload replaces the objects.
  let deleteUndo = null;
  function deleteDraft(d) {
    const id = d.id;
    flushDeletes();
    changed();
    pending.set(id, setTimeout(() => sendDelete(id), UNDO_MS));
    if (S.sel === `d${id}`) {
      if (desk.matches) { unselect(); render(); } else fold();
    } else {
      render();
    }
    deleteUndo = () => {
      if (!pending.has(id)) return;
      clearTimeout(pending.get(id));
      pending.delete(id);
      changed();
      render();
    };
    toast("Draft deleted", { undo: deleteUndo });
  }

  // True once the draft is gone from the server (or was already).
  async function sendDelete(id, keepalive = false) {
    if (!pending.has(id)) return true;
    clearTimeout(pending.get(id));
    try {
      await call("DELETE", `/drafts/${id}`, undefined, keepalive);
    } catch (e) {
      if (e.status !== 404) {
        // Not deleted: it comes back.
        pending.delete(id);
        changed();
        render();
        toast(errorCopy(e, "Could not delete the draft."), { error: true, ms: 8000 });
        return false;
      }
    }
    pending.delete(id);
    changed();
    S.drafts = S.drafts.filter((x) => x.id !== id);
    render();
    return true;
  }

  // Sends the deletes still waiting for their Undo; true if all went.
  async function flushDeletes(keepalive = false) {
    const waiting = [...pending.keys()];
    if (waiting.length && undoAction && undoAction === deleteUndo) hideToast();
    const sent = await Promise.all(waiting.map((id) => sendDelete(id, keepalive)));
    return sent.every(Boolean);
  }
  window.addEventListener("pagehide", () => { flushDeletes(true); });

  // --- Finish review -------------------------------------------------------------
  function openFinish() {
    const show = async () => {
      // What is typed for a new draft is added first, not lost.
      if (S.compose && fields.compose.value.trim()) {
        if (!(await addDraft())) return;
      } else if (S.compose) {
        dropCompose(true);
      }
      go("finish", "m");
    };
    if (S.mode) show();
    else setMode(true, show);
  }

  async function finish() {
    if (sending) return;
    // Not while a decision is on its way: the review would go without it.
    if (deciding) {
      toast("Still saving your decisions. Try again.");
      return;
    }
    if (!reviewCount() && !fields.general.value.trim()) return;
    if (name === null) {
      if (dialog.hidden) askName(() => finish());
      return;
    }
    sending = true;
    if (finishButton && finishButton.isConnected) finishButton.disabled = true;
    try {
      if (!(await flushDeletes())) {
        // A draft meant to go would be published: nothing goes, the text stays.
        toast("Could not delete a draft. Try again.", { error: true, ms: 8000 });
        return;
      }
      const general = fields.general.value;
      const payload = { author: name || "" };
      // Omitted when empty: the review is then the drafts alone.
      if (general.trim()) payload.text = general;
      if (S.toAgent) payload.agent = true;
      const drafts = liveDrafts();
      const onlyProposalApprovals = !general.trim() && drafts.length > 0 && drafts.every((d) =>
        d.kind === "approve" && S.comments.some((c) => c.n === d.of && c.by_agent));
      const sent = S.toAgent && !onlyProposalApprovals;
      const made = await call("POST", "/review", payload);
      S.toAgent = true;
      changed();
      // The comments it made or touched take their new state; accepted ones
      // are closed and leave.
      const byN = new Map(made.map((c) => [c.n, c]));
      const accepted = new Set(liveDrafts().filter((d) => d.kind === "accept").map((d) => d.of));
      S.comments = [...S.comments.filter((c) => !accepted.has(c.n)).map((c) => byN.get(c.n) || c),
        ...made.filter((c) => !S.comments.some((o) => o.n === c.n))];
      S.drafts = [];
      fields.general.value = "";
      S.mode = false;
      S.compose = null;
      S.sel = null;
      S.view = "list";
      S.pos = "strip";
      render();
      toast(sent ? "Review sent to the agent" : "Review published");
      // The server's word on what each comment is now (an acceptance it
      // dropped, say).
      load();
    } catch (e) {
      if (!nameRefused(e)) toast(errorCopy(e, "Could not finish the review."), { error: true, ms: 8000 });
      // The drafts on the server may differ now (one gone, say).
      load();
    } finally {
      sending = false;
      if (finishButton && finishButton.isConnected) finishButton.disabled = !reviewCount() && !fields.general.value.trim();
    }
  }

  // --- Fields ------------------------------------------------------------------
  fields.compose.addEventListener("input", () => {
    grow(fields.compose);
    sendButton.disabled = savingDraft || !fields.compose.value.trim();
  });
  // The frame holds back a tap only while the comment would let go: told
  // again when typing changes that.
  const keptChanged = () => {
    const now = keeps();
    if (now !== kept) { kept = now; drawPins(); }
  };
  fields.edit.addEventListener("input", () => { grow(fields.edit); keptChanged(); });
  // While a reply is typed, Finish review steps aside: no tap meant for the
  // send arrow lands on it.
  for (const f of [fields.reply, fields.general]) {
    f.addEventListener("focus", () => {
      foot.classList.add("typing");
      if (f === fields.reply) toChatEnd();
    });
    f.addEventListener("blur", () => foot.classList.remove("typing"));
  }
  // The send arrow keeps the focus in the field: nothing moves under the
  // finger, and the keyboard stays up.
  replyButton.addEventListener("mousedown", (e) => e.preventDefault());
  fields.reply.addEventListener("input", () => {
    grow(fields.reply);
    keptChanged();
    replyButton.disabled = savingReply || !fields.reply.value.trim();
  });
  // Enter sends only with a hardware keyboard. On a touch screen the
  // keyboard's Enter is a new line, as in the draft's own editor; the send
  // arrow and Add send.
  const sendsOnEnter = (e) => e.key === "Enter" && !e.shiftKey && !e.isComposing
    && !matchMedia("(pointer: coarse)").matches;
  fields.reply.addEventListener("keydown", (e) => {
    if ((desk.matches && e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.isComposing && !e.repeat)
        || (!desk.matches && sendsOnEnter(e))) {
      e.preventDefault();
      addReply();
    }
  });
  fields.general.addEventListener("input", () => {
    const area = fields.general;
    if (!desk.matches && document.activeElement === area) {
      // Measuring with height:auto briefly shrinks the scrollable sheet.
      // Restore its position instead of relying on the IME to reveal the
      // caret again after it commits a word. Follow a newly wrapped line at
      // the end, but don't pull someone editing earlier text to the end.
      const scroll = body.scrollTop;
      const before = area.getBoundingClientRect();
      const viewport = body.getBoundingClientRect();
      const followEnd = area.selectionStart === area.value.length
        && before.bottom >= viewport.top && before.bottom <= viewport.bottom + 1;
      grow(area);
      const added = followEnd ? Math.max(0, area.getBoundingClientRect().height - before.height) : 0;
      body.scrollTop = scroll + added;
    } else grow(area);
    const done = finishButton;
    if (done && done.isConnected) done.disabled = sending || (!reviewCount() && !area.value.trim());
  });
  // Enter adds the draft (Shift+Enter is a new line), with a hardware
  // keyboard only, as above.
  fields.compose.addEventListener("keydown", (e) => {
    if ((desk.matches && e.key === "Enter" && (e.ctrlKey || e.metaKey) && !e.isComposing && !e.repeat)
        || (!desk.matches && sendsOnEnter(e))) {
      e.preventDefault();
      addDraft();
    }
  });
  fields.edit.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      const d = draftById(S.edit);
      if (d) saveEdit(d);
    }
  });

  // --- The sheet: tap and drag -------------------------------------------------------
  // A tap on the strip opens the list. The drag itself is in sheet-drag.js;
  // here is what a height or a tap means for what the sheet shows.
  function openList() {
    go("list", "m");
  }

  const drag = sheetDrag({
    sheet, strip, grip, desk,
    pos: () => S.pos,
    // From the strip the sheet opens on the list as it is pulled.
    open() {
      S.pos = "m";
      S.view = "list";
      render();
    },
    // A drag changes the height only: what the sheet shows stays.
    snap(best) {
      if (best === "strip") {
        if (S.compose) dropCompose(false);
        fold();
      } else go(S.view, best);
    },
    // A tap on the grip steps the height and keeps the view. A tap on the
    // title line does the same on the list; on one comment it goes back to
    // the list; on a new draft's caption or the Finish title it does nothing.
    tap(onGrip) {
      if (!onGrip) {
        if (S.view === "item") { go("list", "m"); return; }
        if (S.view !== "list") return;
      }
      go(S.view, S.pos === "m" ? "f" : "m");
    },
  });

  stripText.setAttribute("role", "button");
  stripText.tabIndex = 0;
  stripText.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      stripText.click();
    }
  });
  grip.addEventListener("keydown", (e) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      go(S.view, S.pos === "m" ? "f" : "m");
    }
  });
  strip.addEventListener("click", (e) => {
    if (e.target.closest("button") || drag.dragged()) return;
    if (desk.matches) go("list");
    else openList();
    if (e.detail === 0) {
      const target = desk.matches ? body.querySelector(".handle") : grip;
      if (target) { if (desk.matches) target.tabIndex = -1; target.focus({ preventScroll: true }); }
    }
  });

  // --- Keys, resizes, coming back ---------------------------------------------------
  function escape() {
    if (!dialog.hidden) closeName(null);
    else if (desktopPanel() && document.body.classList.contains("comments-folded")) return;
    else if (desk.matches && !S.mode) { foldPanel(true); render(); }
    else if (S.compose?.retained && (S.view !== "compose" || !sheet.getClientRects().length ||
        (!desk.matches && S.pos === "strip"))) return;
    else if (S.compose) {
      dropCompose(false);
      if (desk.matches) go("list");
      else fold();
    } else if (S.edit) {
      S.edit = null;
      render();
    } else if (desk.matches && S.view === "list" && S.sel) {
      S.sel = null;
      render();
    } else if (desk.matches ? S.view !== "list" : S.pos !== "strip") {
      if (desk.matches) go("list");
      else {
        fold();
        if (S.pos === "strip") focusFoldTarget();
      }
    }
  }
  document.addEventListener("keydown", (e) => {
    if (!dialog.hidden && e.key === "Tab") {
      const controls = [...dialog.querySelectorAll("input, button")].filter((el) => !el.disabled);
      const i = controls.indexOf(document.activeElement);
      if (i < 0 || (e.shiftKey && i === 0) || (!e.shiftKey && i === controls.length - 1)) {
        e.preventDefault();
        controls[e.shiftKey ? controls.length - 1 : 0].focus();
      }
    }
    if (e.key === "Escape" && !e.defaultPrevented) escape();
  });

  if (typeof ResizeObserver !== "undefined") {
    new ResizeObserver(() => {
      dockToast();
      document.dispatchEvent(new CustomEvent("aa:sheet"));
    }).observe(sheet);
  }

  // Some mobile browsers resize only the visual viewport for the keyboard.
  // Ignore pinch zoom, and remove the override when returning to desktop.
  const visual = window.visualViewport;
  function syncVisualHeight() {
    const usable = visual && !desk.matches && Math.abs(visual.scale - 1) < 0.01;
    const height = usable ? Math.round(visual.height) : 0;
    if (height > 0) document.body.style.setProperty("--visual-height", `${height}px`);
    else document.body.style.removeProperty("--visual-height");
    document.body.classList.toggle("visual-compact", height > 0 && height <= 400);
  }
  if (visual) visual.addEventListener("resize", syncVisualHeight);
  window.addEventListener("resize", syncVisualHeight);
  desk.addEventListener("change", syncVisualHeight);
  syncVisualHeight();

  // The keyboard or a turned phone resizes the page: the pin being written
  // about stays in sight above the sheet.
  let resizeFrame = 0;
  window.addEventListener("resize", () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      fitPins();
      dockToast();
      if (S.compose && S.compose.anchor) reveal("new");
      else if (S.view === "item" && S.sel) {
        reveal(S.sel);
        if (document.activeElement === fields.reply) toChatEnd();
      }
    });
  });
  desk.addEventListener("change", () => {
    foldPanel(desktopPanel() && !S.mode);
    if (!desk.matches && S.pos === "strip" && S.view !== "list") S.pos = DEFAULT_POS[S.view];
    render();
  });
  // Others may have commented while this tab was in the background.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") load();
  });

  // --- Live: the agent's progress and a replaced file ---------------------------
  // The comments are fetched again every few seconds while the page is in
  // sight, so the badges move without a reload. A replaced file is not
  // swapped under the person's finger: a banner offers to show it.
  let fileAt = null;
  const banner = el("div", "banner");
  banner.hidden = true;
  banner.setAttribute("role", "status");
  banner.append(el("span", "", "The file was updated"), button("Show", () => showNewFile(), "btn pri"));
  document.body.append(banner);
  const bannerHeader = document.querySelector("body > header");
  function placeBanner() {
    const bottom = bannerHeader && bannerHeader.getBoundingClientRect().bottom;
    // When the header is hidden, let CSS position the banner in full screen.
    banner.style.top = bottom ? `${bottom + 15}px` : "";
  }
  window.addEventListener("resize", placeBanner);
  new MutationObserver(placeBanner).observe(document.body, { attributes: true, attributeFilter: ["class"] });
  if (bannerHeader) {
    new MutationObserver(placeBanner).observe(bannerHeader, { childList: true, subtree: true, characterData: true });
    if (typeof ResizeObserver !== "undefined") new ResizeObserver(placeBanner).observe(bannerHeader);
  }
  placeBanner();
  // Only the content is loaded again, not the page: a reload would end full
  // screen and fold the sheet. The page is fetched for the file's new name
  // and size; a file of another kind (an image now, say) needs the page
  // itself. The new file's time is taken from the next answer.
  let showing = false;
  async function showNewFile() {
    if (showing) return;
    const content = document.querySelector("main iframe");
    const img = $("image");
    let fresh = null;
    showing = true;
    try {
      const r = await fetch(location.pathname, { cache: "no-store" });
      if (r.ok) fresh = new DOMParser().parseFromString(await r.text(), "text/html");
      else if (r.status !== 404) throw new Error(String(r.status));
    } catch {
      // Offline, say: the page stays as it is, with the banner.
      toast("Could not load the new file. Try again.", { error: true });
      return;
    } finally {
      showing = false;
    }
    const sheetThere = fresh && fresh.getElementById("comments");
    if ((!content && !img) || !sheetThere || sheetThere.dataset.kind !== sheet.dataset.kind) {
      const editing = S.edit && draftById(S.edit);
      if ((S.compose && fields.compose.value) || fields.reply.value ||
          (editing && fields.edit.value !== editing.text)) {
        toast("Save or discard your changes before showing the new file", { error: true, ms: 6000 });
        return;
      }
      location.reload();
      return;
    }
    for (const sel of ["header h1", "header .meta:not(.pm)", "header .meta.pm .pt"]) {
      const now = document.querySelector(sel);
      const then = fresh.querySelector(sel);
      if (now && then) {
        if (sel === "header .meta:not(.pm)") {
          now.replaceChildren(...then.childNodes);
          localDates.apply(now);
        } else now.textContent = then.textContent;
        if (then.title) now.title = then.title;
      }
    }
    const projectButton = document.querySelector("#files-button");
    const freshProjectButton = fresh.querySelector("#files-button");
    if (projectButton && freshProjectButton) projectButton.title = freshProjectButton.title;
    if (fresh.title) document.title = fresh.title;
    banner.hidden = true;
    fileAt = null;
    // Replaced, not pushed: Back still leaves the viewer in one step.
    // Without a #fragment, which would only move within the old content.
    if (content) content.contentWindow.location.replace(content.src.split("#")[0]);
    else {
      const again = fresh.getElementById("image");
      if (again) img.alt = again.alt;
      img.src = `${img.src.split("?")[0]}?v=${Date.now()}`;
    }
    load();
  }
  // The first answer sets what the page shows; a later different one means
  // the file was replaced.
  function noteFile(at) {
    if (at && fileAt === null) fileAt = at;
    else if (at && at !== fileAt) { placeBanner(); banner.hidden = false; }
  }
  let pollSeq = 0;
  let pollStopped = false;
  async function poll() {
    if (pollStopped || document.visibilityState !== "visible" || sending || S.edit || S.compose) return;
    // Not while something is typed: a redraw would take the keyboard away.
    if (Object.values(fields).includes(document.activeElement)) return;
    // A poll never cancels a load or a change: it checks they did not land.
    const mine = generation;
    const seq = ++pollSeq;
    let data;
    try {
      data = await fetchComments();
    } catch (e) {
      if (e.status === 404) pollStopped = true;
      return;
    }
    if (mine !== generation || seq !== pollSeq || !Array.isArray(data)) return;
    if (S.loaded && JSON.stringify(data) === JSON.stringify(S.comments)) return;
    S.loaded = true;
    S.comments = data;
    if (S.sel && !items().some((i) => i.key === S.sel)) unselect();
    render();
  }
  setInterval(poll, 8000);

  foldPanel(desktopPanel() && !S.mode);
  render();
  load();
})();
