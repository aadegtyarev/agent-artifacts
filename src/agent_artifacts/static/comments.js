// Comments on the viewer page: the header button, the panel (a bottom sheet
// over a strip on a phone) with the list and a form for the whole file, the
// composer at a spot clicked on an image or a page, pins, a comment's action
// menu, typing mode on a phone, and the toasts with Undo. Names and texts come
// from anyone with the link, so they go in as text nodes only, never as HTML.
(() => {
  const panel = document.getElementById("comments");
  if (!panel) return;
  const $ = (id) => document.getElementById(id);
  const artifact = panel.dataset.artifact;
  const base = `/a/${artifact}/comments`;
  const toggle = $("comments-toggle");
  const count = $("comments-count");
  const strip = $("comments-strip");
  const stripText = $("strip-text");
  const list = $("comment-list");
  const resolvedList = $("resolved-list");
  const showResolved = $("show-resolved");
  const form = $("comment-form");
  const author = $("comment-author");
  const text = $("comment-text");
  const send = $("comment-send");
  const composer = $("composer");
  const image = $("image");
  const imageBox = $("image-box");
  const pins = $("pins");
  const frame = $("frame");
  const toastBox = $("toast");
  const toastText = $("toast-text");
  const toastUndo = $("toast-undo");

  // How a comment is tied to the content of this kind of file: "point" on an
  // image, "element" on a page (html, md, txt), null for a comment about the
  // whole file.
  function pinMode(kind) {
    if (!composer) return null;
    if (["png", "jpeg", "gif", "webp"].includes(kind) && image) return "point";
    return ["html", "md", "txt"].includes(kind) && frame ? "element" : null;
  }
  const mode = pinMode(panel.dataset.kind);
  // A page's pins are drawn by the script inside its sandboxed frame (see
  // pins.js). Until it reports ready the page has comments without pins;
  // `off` once it is clearly not coming. `missing`: comments whose spot the
  // script did not find; `spot`: the draft pin, in the frame's pixels;
  // `picking`: the script was told that a click on the page marks a spot.
  const page = { ready: false, off: false, missing: new Set(), spot: null, picking: false };

  // Browser storage may be missing or refuse; then the name is not
  // remembered and comments cannot be edited from this browser.
  const storage = {
    get(key) {
      try { return localStorage.getItem(key); } catch { return null; }
    },
    set(key, value) {
      try { localStorage.setItem(key, value); return true; } catch { return false; }
    },
    remove(key) {
      try { localStorage.removeItem(key); } catch { /* nothing to drop */ }
    },
  };
  const NAME_KEY = "agent-artifacts:name";
  const tokenKey = (n) => `agent-artifacts:token:${artifact}:${n}`;

  let comments = [];
  let resolvedShown = false;
  // The spot of the comment being written in the composer, or null.
  let draft = null;
  // {n, draft, start, end} while a comment's text is being edited; kept
  // here so a redraw of the list does not lose the text or the caret.
  let editing = null;
  // The comment and the pin highlighted for a moment, redrawn lit.
  const lit = { comment: null, pin: null };
  // A delete waiting out its Undo time: {c, token, timer}. Its DELETE goes
  // when the time is up, on the next action, or when the page is left.
  let pending = null;
  // Numbers whose DELETE is on its way; hidden until the server answers.
  const deleting = new Set();
  // Every change to `comments` bumps it, so an older GET cannot overwrite it.
  let generation = 0;
  const timers = {};
  const phone = window.matchMedia("(max-width: 640px)");
  // A finger, not a mouse: typing mode is for an on-screen keyboard.
  const touch = window.matchMedia("(pointer: coarse)");
  const UNDO_MS = 6000;

  class ApiError extends Error {
    constructor(status, code, message) {
      super(message);
      this.status = status;
      this.code = code;
    }
  }

  async function call(method, url, body, headers = {}, keepalive = false) {
    const init = { method, headers: { ...headers }, cache: "no-store" };
    // Survives the page being closed, for a delete sent on the way out.
    if (keepalive) init.keepalive = true;
    if (body !== undefined) {
      init.headers["Content-Type"] = "application/json";
      init.body = JSON.stringify(body);
    }
    let response;
    try {
      response = await fetch(url, init);
    } catch {
      throw new ApiError(0, "network", "Could not reach the server. Check the connection and try again.");
    }
    if (response.status === 204) return null;
    let data = null;
    try { data = await response.json(); } catch { /* not JSON, e.g. a proxy's page */ }
    if (response.ok && data !== null) return data;
    if (response.status === 429) {
      throw new ApiError(429, "rate_limited", "Too many requests from your address. Wait a minute and try again.");
    }
    const message = data && typeof data.message === "string"
      ? data.message.charAt(0).toUpperCase() + data.message.slice(1) + "."
      : `The server answered ${response.status}. Try again later.`;
    throw new ApiError(response.status, data && data.error, message);
  }

  // A message in the panel ("comments-status"), under a form ("form-status",
  // "composer-status"); a success fades after a few seconds, an error stays.
  function say(where, message, error = false) {
    const node = $(where);
    if (!node) return;
    clearTimeout(timers[where]);
    node.textContent = message;
    node.classList.toggle("error", error);
    if (message && !error) timers[where] = setTimeout(() => { node.textContent = ""; }, 4000);
  }

  // A toast over the page, with an Undo button when `undo` is given. When it
  // goes with focus on its Undo, focus returns to the element `back` (an id).
  let undoAction = null;
  let toastBack = null;
  function toast(message, { undo = null, ms = 4000, error = false, back = null } = {}) {
    clearTimeout(timers.toast);
    toastBack = back;
    toastText.textContent = message;
    toastBox.classList.toggle("error", error);
    undoAction = undo;
    toastUndo.hidden = !undo;
    toastBox.classList.add("on");
    document.body.classList.add("toast-on");
    dockToast();
    timers.toast = setTimeout(hideToast, ms);
  }

  // On a phone a toast sits right above whatever holds the bottom: the
  // docked composer, the form of the open sheet, or the strip.
  function dockToast() {
    toastBox.style.top = "";
    if (typingHost) {
      // In typing mode the bottom may be under the keyboard: right below
      // the form at the top instead.
      toastBox.style.bottom = "auto";
      toastBox.style.top = `${typingHost.getBoundingClientRect().bottom + 8}px`;
      return;
    }
    if (!phone.matches) {
      toastBox.style.bottom = "";
      return;
    }
    let edge = window.innerHeight;
    const docked = composer && !composer.hidden && composer.classList.contains("dock");
    const holder = docked ? composer : panel.hidden ? strip : form;
    const top = holder.getBoundingClientRect().top;
    if (top > 0) edge = top;
    toastBox.style.bottom = `${Math.max(0, window.innerHeight - edge) + 4}px`;
  }

  function hideToast() {
    clearTimeout(timers.toast);
    const focused = toastBox.contains(document.activeElement);
    toastBox.classList.remove("on");
    document.body.classList.remove("toast-on");
    undoAction = null;
    toastUndo.hidden = true;
    if (focused) focusBack(toastBack);
  }

  // Focus to the element `id` if it is shown, else to the panel's title or
  // the button that opens the panel.
  function focusBack(id) {
    const target = id && $(id);
    if (target && target.getClientRects().length) target.focus({ preventScroll: true });
    else (panel.hidden ? (phone.matches ? strip : toggle) : $("comments-title")).focus({ preventScroll: true });
  }

  // The comment row next to `n` (or before it), where focus goes when `n`
  // leaves the list.
  function neighbour(n) {
    const li = $(`comment-${n}`);
    const next = li && (li.nextElementSibling || li.previousElementSibling);
    return next ? next.id : null;
  }

  toastUndo.addEventListener("click", () => {
    const action = undoAction;
    hideToast();
    if (action) action();
  });

  function el(tag, className, content) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (content !== undefined) node.textContent = content;
    return node;
  }

  // A comment field is one line while empty and grows with its text, up to
  // the style sheet's max-height.
  function grow(area) {
    area.style.height = "auto";
    area.style.height = `${area.scrollHeight + 2}px`;
  }

  function button(label, onClick, className = "text") {
    const b = el("button", className, label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  const ICONS = {
    check: "m5 12 5 5 9-10",
    reopen: "M9 14 4 9l5-5M4 9h11a5 5 0 0 1 0 10h-3",
    edit: "M4 20h4L19 9l-4-4L4 16zM13 7l4 4",
    delete: "M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13",
    up: "m6 15 6-6 6 6",
    down: "m6 9 6 6 6-6",
    more: "M5 12h.01M12 12h.01M19 12h.01",
    copy: "M9 9h10v11H9zM5 15V4h10",
    link: "M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1",
  };
  function icon(name) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", ICONS[name]);
    svg.append(path);
    return svg;
  }

  // A button that shows only an icon; its name is in aria-label and title.
  function iconButton(name, label, onClick) {
    const b = button("", onClick, "icon");
    b.append(icon(name));
    b.setAttribute("aria-label", label);
    b.title = label.replace(/ comment \d+$/, "");
    return b;
  }

  // "Comment 3" in the panel and on a computer; "#3" in a phone's toast.
  const named = (n) => (phone.matches ? `#${n}` : `Comment ${n}`);

  function when(iso) {
    const t = new Date(iso);
    const seconds = (Date.now() - t.getTime()) / 1000;
    if (seconds < 60) return "just now";
    if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`;
    if (seconds < 86400) return `${Math.floor(seconds / 3600)} h ago`;
    const options = { month: "short", day: "numeric" };
    if (t.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
    return t.toLocaleDateString("en", options);
  }

  // Resolved comments keep their pins, shown grey only with the resolved list.
  const pinned = (c) => mode && c.anchor && c.anchor.type === mode && (!c.resolved || resolvedShown);
  const hasPin = (c) => pinned(c) && (mode === "point" || (page.ready && !page.missing.has(c.n)));
  // The list without comments deleted a moment ago (Undo still possible).
  const visible = () => comments.filter((c) => !deleting.has(c.n) && !(pending && pending.c.n === c.n));

  // --- Loading and drawing --------------------------------------------------

  async function load() {
    const mine = ++generation;
    if (!comments.length) say("comments-status", "Loading comments…");
    let found;
    try {
      found = await call("GET", base);
    } catch (e) {
      if (mine === generation) say("comments-status", `Could not load comments. ${e.message}`, true);
      return;
    }
    if (mine !== generation) return; // something newer has landed meanwhile
    comments = found;
    say("comments-status", "");
    dropStaleTokens();
    render();
    showLinked();
  }

  // A link copied from a comment's menu ends in #comment-N: the first load
  // shows that comment.
  let linkShown = false;
  function showLinked() {
    if (linkShown) return;
    linkShown = true;
    const match = /^#comment-(\d+)$/.exec(window.location.hash);
    if (match && visible().some((c) => c.n === Number(match[1]))) showComment(Number(match[1]));
  }

  // Tokens of this file's comments that are gone, deleted by the agent.
  function dropStaleTokens() {
    const prefix = tokenKey("");
    const known = new Set(comments.map((c) => tokenKey(c.n)));
    try {
      const keys = Object.keys(localStorage).filter((k) => k.startsWith(prefix) && !known.has(k));
      for (const key of keys) localStorage.removeItem(key);
    } catch { /* no storage, no tokens */ }
  }

  function changed(next) {
    generation++;
    comments = next;
  }

  // Redraws the list from `comments`, keeping focus on the same comment and
  // an open editor's text and caret.
  function render() {
    const active = document.activeElement;
    const editorFocused = active && active.matches(".comment-edit textarea");
    if (editorFocused && editing) {
      editing.start = active.selectionStart;
      editing.end = active.selectionEnd;
    }
    let focusedItem = active && active.matches(".comment") ? active.id : null;
    if (editorFocused && editing) focusedItem = `comment-${editing.n}`;
    const shown = visible();
    if (editing && !shown.some((c) => c.n === editing.n)) {
      if (!comments.some((c) => c.n === editing.n)) say("comments-status", `Comment ${editing.n} was deleted.`, true);
      editing = null;
    }
    const open = shown.filter((c) => !c.resolved);
    const resolved = shown.filter((c) => c.resolved);
    count.textContent = open.length;
    toggle.setAttribute("aria-label", `Comments, ${open.length} open`);
    // The strip: all comments and the resolved ones. The agent's status
    // will go next to it (#strip-agent) once the server reports one.
    stripText.textContent = shown.length ? `${shown.length} · ${resolved.length} resolved` : "No comments yet";
    strip.setAttribute("aria-label", shown.length
      ? `Comments: ${shown.length}, ${resolved.length} resolved. Open`
      : "No comments yet. Open comments");
    $("comments-title").textContent = `Comments · ${open.length} open`;
    $("comments-empty").hidden = shown.length > 0;
    const hint = $("pin-hint");
    hint.hidden = !page.ready;
    hint.textContent = `${phone.matches ? "Tap" : "Click"} the page to pin a comment`;
    if (!resolved.length) resolvedShown = false;
    list.replaceChildren(...open.map(item));
    resolvedList.replaceChildren(...resolved.map(item));
    showResolved.hidden = resolved.length === 0;
    // Compact, to share the head row with the title: the chevron shows
    // whether the resolved list is open (aria-expanded says it too).
    showResolved.replaceChildren(`Resolved (${resolved.length})`, icon(resolvedShown ? "up" : "down"));
    showResolved.title = `${resolvedShown ? "Hide" : "Show"} resolved comments`;
    showResolved.setAttribute("aria-expanded", String(resolvedShown));
    resolvedList.hidden = !resolvedShown;
    drawPins();
    if (editing && editorFocused) {
      const box = $(`comment-${editing.n}`).querySelector(".comment-edit");
      // In typing mode the new editor takes the old one's place first, or
      // it is hidden and cannot take the focus.
      if (typingHost && !typingHost.isConnected) enterTyping(box);
      const area = box.querySelector("textarea");
      grow(area);
      area.focus({ preventScroll: true });
      area.setSelectionRange(editing.start, editing.end);
    } else {
      // The edit typed in is gone (the comment deleted or read meanwhile).
      // Not every browser sends focusout for a removed field, so typing
      // mode does not wait for one.
      if (typingHost && !typingHost.isConnected) exitTyping();
      if (focusedItem && $(focusedItem)) $(focusedItem).focus({ preventScroll: true });
    }
  }

  function item(c) {
    const li = el("li", "comment");
    li.id = `comment-${c.n}`;
    li.tabIndex = -1;
    if (c.resolved) li.classList.add("resolved");
    if (lit.comment === c.n) li.classList.add("lit");
    // A comment the agent has read is final: this browser's token is useless.
    let token = storage.get(tokenKey(c.n));
    if (token && c.read) {
      storage.remove(tokenKey(c.n));
      token = null;
    }
    if (editing && editing.n === c.n && (c.read || !token)) {
      editing = null;
      say("comments-status", c.read
        ? `The agent has already read comment ${c.n}; it can no longer be changed.`
        : `Comment ${c.n} cannot be changed from this browser.`, true);
    }
    // A click anywhere on the comment but its buttons and editor shows its pin.
    li.addEventListener("click", (e) => {
      if (e.target.closest("button, textarea, input, a")) return;
      showPinOf(c.n);
    });

    const meta = el("div", "comment-meta");
    const badge = hasPin(c)
      ? button(String(c.n), () => showPinOf(c.n), "badge")
      : el("span", "badge", String(c.n));
    if (hasPin(c)) badge.setAttribute("aria-label", `Show the pin of comment ${c.n}`);
    if (c.resolved) {
      badge.classList.add("resolved");
      badge.append(icon("check"));
    }
    meta.append(badge);
    meta.append(el("span", "author", c.author));
    const time = el("time", "", when(c.created_at));
    time.dateTime = c.created_at;
    time.title = new Date(c.created_at).toLocaleString();
    meta.append(time);
    if (c.resolved) meta.append(el("span", "tag", "resolved"));
    if (c.before_replace) meta.append(el("span", "tag", "before update"));
    if (c.read) meta.append(el("span", "tag", "read by the agent"));
    if (c.edited_at) meta.append(el("span", "tag", "edited"));
    // The actions sit at the right of the meta line, as icon buttons.
    const head = el("div", "comment-head");
    head.append(meta);
    if (editing && editing.n === c.n) {
      li.append(head, editor(c, token));
      return li;
    }
    const actions = el("div", "comment-actions");
    // On a phone the ⋯ button stands in for the icon buttons; the same menu
    // opens on a long press, and on a computer on a right click.
    // On a computer it comes last and shows on hover or keyboard focus,
    // for Copy text and Copy link.
    const more = iconButton("more", `Actions for comment ${c.n}`, (e) => openMenu(c, e.currentTarget));
    more.classList.add("more");
    more.setAttribute("aria-haspopup", "menu");
    more.setAttribute("aria-expanded", "false");
    actions.append(c.resolved
      ? iconButton("reopen", `Reopen comment ${c.n}`, (e) => resolve(c, e.currentTarget))
      : iconButton("check", `Resolve comment ${c.n}`, (e) => resolve(c, e.currentTarget)));
    if (token && !c.read) {
      actions.append(iconButton("edit", `Edit comment ${c.n}`, () => startEdit(c)));
      actions.append(iconButton("delete", `Delete comment ${c.n}`, () => remove(c, token)));
    }
    actions.append(more);
    head.append(actions);
    li.append(head, el("p", "comment-text", c.text));
    // On a page without its pin: the text of the element it was about.
    const lost = page.ready && page.missing.has(c.n);
    if (mode === "element" && c.anchor && (page.off || lost)) {
      const where = el("p", "note where");
      if (lost) where.append(el("span", "lost", "Spot not found"), " · ");
      else where.append("On ");
      where.append(c.anchor.text ? `“${c.anchor.text}”` : "an element of the page");
      li.append(where);
    }
    return li;
  }

  function editor(c, token) {
    const box = el("div", "comment-edit");
    box.dataset.n = c.n;
    const area = el("textarea");
    area.value = editing.draft;
    area.maxLength = 2000;
    area.rows = 1;
    area.setAttribute("aria-label", `Text of comment ${c.n}`);
    area.addEventListener("input", () => { editing.draft = area.value; });
    const save = button("Save", () => saveEdit(c, token, save), "text typing-send");
    area.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save.click(); }
      if (e.key === "Escape") { e.stopPropagation(); cancelEdit(c.n); }
    });
    // In typing mode Cancel goes back to the page with a changed text kept.
    const cancel = button("Cancel", () => {
      if (typingHost === box && editing.draft !== c.text) leaveTyping();
      else cancelEdit(c.n);
    }, "text typing-cancel");
    const tools = el("div", "comment-tools");
    tools.append(save, cancel);
    const status = el("p", "note");
    status.id = "edit-status";
    status.setAttribute("role", "status");
    status.setAttribute("aria-live", "polite");
    box.append(area, tools, status);
    return box;
  }

  function drawPins() {
    if (mode === "element") {
      sendPins();
      return;
    }
    if (!mode) return;
    const shown = visible().filter(hasPin).map((c) => {
      const pin = pinAt(c.anchor, String(c.n));
      pin.dataset.n = c.n;
      if (c.resolved) pin.classList.add("resolved");
      if (lit.pin === c.n) pin.classList.add("lit");
      pin.setAttribute("aria-label", `Comment ${c.n}${c.resolved ? ", resolved" : ""}`);
      return pin;
    });
    if (draft) {
      // Unnumbered: the server hands out the number on sending.
      const pin = pinAt(draft, "+");
      pin.classList.add("new");
      pin.setAttribute("aria-label", "The spot of the comment you are writing");
      shown.push(pin);
    }
    pins.replaceChildren(...shown);
    pins.hidden = shown.length === 0;
    fitPins();
  }

  // On a phone a pin's tap area is larger than the pin, so the areas of
  // pins close together overlap: a tap goes to the pin nearest to it.
  function nearestPin(x, y) {
    let best = null;
    let distance = Infinity;
    for (const pin of pins.children) {
      const r = pin.getBoundingClientRect();
      const d = (r.left + r.width / 2 - x) ** 2 + (r.top + r.height / 2 - y) ** 2;
      if (d < distance) { distance = d; best = pin; }
    }
    return best;
  }

  if (mode === "point") {
    pins.addEventListener("click", (e) => {
      const hit = e.target.closest(".pin");
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      // A keyboard press has no point (detail 0): it is the focused pin.
      const pin = e.detail ? nearestPin(e.clientX, e.clientY) || hit : hit;
      if (!pin.classList.contains("new")) showComment(Number(pin.dataset.n));
      else if (minimized) closePanel(); // brings the composer back
      else $("composer-text").focus();
    });
  }

  // The script in the frame draws them; it gets numbers, anchors and flags,
  // never texts or names.
  function sendPins() {
    if (!page.ready) return;
    tell({
      type: "pins",
      pins: visible().filter(pinned).map((c) => ({
        n: c.n, anchor: c.anchor, resolved: c.resolved, lit: lit.pin === c.n,
      })),
      draft,
    });
  }

  const tell = (m) => frame.contentWindow.postMessage(m, "*");

  function pinAt(anchor, label) {
    const pin = el("button", "pin", label);
    pin.type = "button";
    // Percentages of the layer, which is the size of the image, so pins
    // follow it when the window or the panel changes its size.
    pin.style.left = `${anchor.x * 100}%`;
    pin.style.top = `${anchor.y * 100}%`;
    return pin;
  }

  // The pin layer covers exactly the displayed image. A pin too close to
  // the right or top edge turns to the other side to stay on the image.
  function fitPins() {
    if (mode !== "point") return;
    const width = image.offsetWidth;
    const height = image.offsetHeight;
    pins.style.left = `${image.offsetLeft}px`;
    pins.style.top = `${image.offsetTop}px`;
    pins.style.width = `${width}px`;
    pins.style.height = `${height}px`;
    for (const pin of pins.children) {
      const x = parseFloat(pin.style.left) / 100;
      const y = parseFloat(pin.style.top) / 100;
      pin.classList.toggle("left", x * width + pin.offsetWidth > width && x * width >= pin.offsetWidth);
      pin.classList.toggle("down", y * height < pin.offsetHeight);
    }
  }

  // Lights the comment or pin `n` for a moment; the state survives redraws.
  function light(which, n) {
    lit[which] = n;
    clearTimeout(timers[which]);
    timers[which] = setTimeout(() => {
      lit[which] = null;
      for (const node of document.querySelectorAll(which === "pin" ? ".pin.lit" : ".comment.lit")) {
        node.classList.remove("lit");
      }
      if (which === "pin" && mode === "element") sendPins();
    }, 2500);
  }

  // Brings the pin of comment `n` (null: the draft) into view; on a page
  // the script in the frame scrolls it.
  function centerOn(n) {
    if (mode === "element") tell({ type: "show", n: n === null ? 0 : n });
    else centerPin(pins.querySelector(n === null ? ".pin.new" : `[data-n="${n}"]`));
  }

  // Scrolls the image so the pin sits in the middle of what is visible of
  // it: above the sheet (the content shrinks above it) or the docked composer.
  function centerPin(pin) {
    if (!pin) return;
    const box = imageBox.getBoundingClientRect();
    let bottom = box.bottom;
    if (!composer.hidden && composer.classList.contains("dock")) {
      bottom = Math.min(bottom, composer.getBoundingClientRect().top);
    }
    const r = pin.getBoundingClientRect();
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    imageBox.scrollBy({
      top: r.top + r.height / 2 - (box.top + bottom) / 2,
      left: r.left + r.width / 2 - (box.left + box.right) / 2,
      behavior: smooth ? "smooth" : "auto",
    });
  }

  // From a comment to its pin; the panel or sheet stays as it is.
  function showPinOf(n) {
    const c = comments.find((x) => x.n === n);
    light("comment", n);
    if (!c || !hasPin(c)) {
      render();
      return;
    }
    light("pin", n);
    render();
    centerOn(n);
  }

  // From a pin to its comment, opening the panel or sheet.
  function showComment(n) {
    if (panel.hidden) openPanel(false);
    const c = comments.find((x) => x.n === n);
    if (c && c.resolved) resolvedShown = true;
    light("comment", n);
    light("pin", n);
    render();
    const li = $(`comment-${n}`);
    if (!li) return;
    li.scrollIntoView({ block: "nearest" });
    li.focus({ preventScroll: true });
  }

  // --- The composer at a spot on the image or the page ----------------------

  function placeComposer() {
    if (!mode || composer.hidden) return;
    const onImage = mode === "point";
    if (phone.matches) {
      // Docked to the bottom of the page by the style sheet; while it is
      // typed in on a touch screen it is at the top instead (typing mode).
      composer.classList.add("dock");
      composer.style.left = "";
      composer.style.top = "";
      if (typingHost === composer) return;
      if (!onImage) {
        dockToast();
        return;
      }
      // Room under the image, so the pin can scroll up above the composer.
      const covered = imageBox.getBoundingClientRect().bottom - composer.getBoundingClientRect().top;
      imageBox.style.paddingBottom = covered > 0 ? `${covered + 8}px` : "";
      dockToast();
      return;
    }
    dockToast();
    // Next to the pin, inside the window: below it, or above with no room.
    composer.classList.remove("dock");
    const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
    let x;
    let y;
    if (onImage) {
      imageBox.style.paddingBottom = "";
      const img = image.getBoundingClientRect();
      const box = imageBox.getBoundingClientRect();
      x = clamp(img.left + draft.x * img.width, box.left, box.right);
      y = clamp(img.top + draft.y * img.height, box.top, box.bottom);
    } else {
      // The frame reports where the draft pin is in its own pixels.
      const box = frame.getBoundingClientRect();
      const spot = page.spot || { x: box.width / 2, y: 0 };
      x = clamp(box.left + spot.x, box.left, box.right);
      y = clamp(box.top + spot.y, box.top, box.bottom);
    }
    const w = composer.offsetWidth;
    const h = composer.offsetHeight;
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    let top = y + 12;
    if (top + h > vh - 12) top = y - h - 36; // the pin itself stands above the spot
    composer.style.left = `${clamp(x - w / 2, 12, vw - w - 12)}px`;
    composer.style.top = `${clamp(top, 12, Math.max(12, vh - h - 12))}px`;
  }

  // On a phone the composer and the sheet never show together: the sheet
  // opened over a composer hides it with its draft (the "+" pin stays), and
  // folding the sheet brings it back.
  let minimized = false;

  function minimizeComposer() {
    minimized = true;
    composer.hidden = true;
    if (mode === "point") imageBox.style.paddingBottom = "";
  }

  function restoreComposer() {
    minimized = false;
    composer.hidden = false;
    placeComposer();
    $("composer-text").focus({ preventScroll: true });
  }

  function openComposer(anchor, spot = null) {
    // A new draft makes an older discarded one's Undo meaningless.
    if (undoAction && undoAction.discarded) hideToast();
    // On a phone the composer takes the bottom; the sheet folds to the strip.
    if (phone.matches && !panel.hidden) closePanel();
    const wasOpen = !composer.hidden;
    draft = anchor;
    page.spot = spot;
    lit.pin = null;
    const on = $("composer-on");
    on.hidden = mode !== "element";
    on.textContent = anchor.text ? `On “${anchor.text}”` : "On the marked element";
    if (!wasOpen) {
      const name = $("composer-author");
      if (!name.value) name.value = storage.get(NAME_KEY) || "";
      say("composer-status", "");
      composer.hidden = false;
    }
    drawPins();
    placeComposer();
    $("composer-text").focus({ preventScroll: true });
    if (phone.matches && !typingHost) requestAnimationFrame(() => centerOn(null));
  }

  // Closes the composer; focus goes to the content unless the caller moves it.
  function closeComposer() {
    const inside = composer.contains(document.activeElement);
    if (typingHost === composer) exitTyping();
    composer.hidden = true;
    minimized = false;
    draft = null;
    page.spot = null;
    $("composer-text").value = "";
    $("composer-text").style.height = "";
    if (mode === "point") {
      if (inside) imageBox.focus({ preventScroll: true });
      imageBox.style.paddingBottom = "";
    } else if (inside) {
      focusBack(null);
    }
    dockToast();
    say("composer-status", "");
    drawPins();
  }

  // Cancel or Escape. Typed text is not lost silently: Undo brings the
  // composer back with the same spot and text.
  function cancelComposer() {
    const kept = { anchor: draft, spot: page.spot, text: $("composer-text").value };
    closeComposer();
    if (!kept.text.trim()) return;
    const restore = () => {
      openComposer(kept.anchor, kept.spot);
      $("composer-text").value = kept.text;
      grow($("composer-text"));
    };
    restore.discarded = true;
    toast("Comment discarded", {
      undo: restore,
      ms: UNDO_MS,
      back: mode === "point" ? "image-box" : null,
    });
    toastUndo.focus({ preventScroll: true });
  }

  // Names are one line; the text keeps its line breaks.
  function takeName(input) {
    const name = input.value.replace(/[\r\n\t]+/g, " ").trim();
    input.value = name;
    storage.set(NAME_KEY, name);
    author.value = name;
    if (composer) $("composer-author").value = name;
    return name;
  }

  async function post(body) {
    const posted = await call("POST", base, body);
    const { edit_token: token, ...c } = posted;
    const kept = storage.set(tokenKey(c.n), token);
    changed([...comments, c]);
    return { c, kept };
  }

  const sentMessage = (n, kept) => (kept
    ? `Comment ${n} sent. You can edit or delete it until the agent reads it.`
    : `Comment ${n} sent. This browser cannot store it, so it cannot be edited here.`);

  if (mode) {
    const composerText = $("composer-text");
    const composerSend = $("composer-send");
    composer.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (composerSend.disabled) return;
      if (!composerText.value.trim()) {
        say("composer-status", "Write a comment first.", true);
        composerText.focus();
        return;
      }
      flushPending();
      const body = { author: takeName($("composer-author")), text: composerText.value, anchor: draft };
      await busy(composerSend, "Sending…", async () => {
        try {
          const { c, kept } = await post(body);
          closeComposer();
          light("comment", c.n);
          light("pin", c.n);
          render();
          // Focus on the new comment: its row, or its pin with the panel closed.
          const row = $(`comment-${c.n}`);
          if (row && row.getClientRects().length) row.focus({ preventScroll: true });
          else if (mode === "point") pins.querySelector(`[data-n="${c.n}"]`).focus({ preventScroll: true });
          else focusBack(null);
          toast(sentMessage(c.n, kept), { ms: 5000 });
        } catch (err) {
          say("composer-status", err.message, true);
        }
      });
    });
    composerText.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
        e.preventDefault();
        composer.requestSubmit();
      }
    });
    // In typing mode Cancel goes back to the page with typed text kept; an
    // empty composer closes.
    $("composer-cancel").addEventListener("click", () => {
      if (typingHost === composer && composerText.value.trim()) leaveTyping();
      else cancelComposer();
    });
    window.addEventListener("resize", placeComposer);
    phone.addEventListener("change", () => {
      // A computer has room for both: a hidden draft comes back.
      if (minimized && !phone.matches) restoreComposer();
      placeComposer();
    });
  }

  if (mode === "point") {
    // A click places the spot (or moves it while writing); a Ctrl/Cmd or
    // middle click still opens the image full size.
    $("image-link").addEventListener("click", (e) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey) return;
      e.preventDefault();
      const box = image.getBoundingClientRect();
      if (!box.width || !box.height) return;
      const fraction = (v) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000;
      openComposer({
        type: "point",
        x: fraction((e.clientX - box.left) / box.width),
        y: fraction((e.clientY - box.top) / box.height),
      });
    });
    image.addEventListener("load", fitPins);
    // The box too: as the sheet grows or shrinks, the image moves in it.
    if (window.ResizeObserver) {
      const watch = new ResizeObserver(() => { fitPins(); placeComposer(); });
      watch.observe(image);
      watch.observe(imageBox);
    }
    window.addEventListener("resize", fitPins);
    imageBox.addEventListener("scroll", () => { if (!phone.matches) placeComposer(); });
    $("form-note").hidden = false;
  }

  // --- Pins on a page: the script in the frame -------------------------------
  // While the panel (a phone's sheet) is open, a click on the page marks the
  // spot of a new comment and does nothing else; folded, the page works as
  // usual.

  // Tells the script whether the next click is a pin's.
  function syncPicking() {
    if (mode !== "element" || !page.ready) return;
    const on = !panel.hidden;
    if (on !== page.picking) tell({ type: "mode", on });
    page.picking = on;
  }

  // Messages from the frame: only from its window, checked like any input.
  const isAnchor = (a) => a && typeof a === "object" && a.type === "element"
    && typeof a.selector === "string" && a.selector.length <= 512
    && typeof a.text === "string" && a.text.length <= 200
    && [a.x, a.y].every((v) => typeof v === "number" && v >= 0 && v <= 1)
    && Number.isInteger(a.width) && a.width > 0 && a.width <= 100000;
  const isSpot = (p) => p && typeof p === "object"
    && [p.x, p.y].every((v) => typeof v === "number" && Number.isFinite(v));

  function onFrameMessage(e) {
    if (e.source !== frame.contentWindow) return;
    const m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.type === "ready") {
      clearTimeout(timers.ready);
      page.ready = true;
      page.off = false;
      // A page loaded again starts with pin mode off.
      page.picking = false;
      syncPicking();
      $("form-note").hidden = false;
      render();
    } else if (!page.ready) {
      // Nothing else counts before the script has said it is there.
    } else if (m.type === "anchor" && page.picking && isAnchor(m.anchor)) {
      const { selector, text: t, x, y, width } = m.anchor;
      openComposer({ type: "element", selector, text: t, x, y, width }, isSpot(m.spot) ? m.spot : null);
    } else if (m.type === "placed" && Array.isArray(m.missing) && m.missing.length <= 200
      && m.missing.every(Number.isInteger) && (m.spot === null || isSpot(m.spot))) {
      const missing = new Set(m.missing);
      const same = missing.size === page.missing.size && [...missing].every((n) => page.missing.has(n));
      page.spot = m.spot;
      placeComposer();
      if (same) return;
      page.missing = missing;
      render();
    } else if (m.type === "pin" && Number.isInteger(m.n)) {
      if (m.n === 0) {
        if (minimized) closePanel(); // brings the composer back
        else if (!composer.hidden) $("composer-text").focus();
      } else if (comments.some((c) => c.n === m.n)) {
        showComment(m.n);
      }
    } else if (m.type === "escape") {
      escape();
    }
  }

  // The script may have reported ready before this page listened; asked
  // here and when the frame has loaded, it answers. With no answer soon
  // after, the page blocks it: comments without pins.
  function waitForScript(ms) {
    clearTimeout(timers.ready);
    if (page.ready) return;
    timers.ready = setTimeout(() => {
      page.off = true;
      render();
    }, ms);
  }

  if (mode === "element") {
    window.addEventListener("message", onFrameMessage);
    frame.addEventListener("load", () => {
      tell({ type: "hello" });
      waitForScript(2000);
    });
    tell({ type: "hello" });
    waitForScript(4000);
  }

  // --- The form for the whole file, folded to one button until needed --------

  const fold = (yes) => form.classList.toggle("folded", yes);

  $("comment-open").addEventListener("click", () => {
    fold(false);
    text.focus();
  });
  // Folds again when left empty.
  form.addEventListener("focusout", () => {
    setTimeout(() => {
      if (form.contains(document.activeElement) || text.value.trim()) return;
      fold(true);
    }, 0);
  });

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (send.disabled) return;
    if (!text.value.trim()) {
      say("form-status", "Write a comment first.", true);
      text.focus();
      return;
    }
    flushPending();
    const body = { author: takeName(author), text: text.value, anchor: null };
    await busy(send, "Sending…", async () => {
      try {
        const { c, kept } = await post(body);
        text.value = "";
        text.style.height = "";
        if (typingHost === form) exitTyping();
        fold(true);
        light("comment", c.n);
        render();
        say("form-status", sentMessage(c.n, kept));
        $(`comment-${c.n}`).scrollIntoView({ block: "nearest" });
      } catch (err) {
        say("form-status", err.message, true);
      }
    });
  });

  text.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  // Shown in typing mode only: back to the sheet, the typed text kept.
  $("comment-cancel").addEventListener("click", () => { if (typingHost) leaveTyping(); });

  // --- Actions --------------------------------------------------------------

  const lostReason = (n, e) => (e.code === "already_read"
    ? `the agent has already read comment ${n}`
    : `comment ${n} cannot be changed from this browser`);

  // A token-holder's edit that the server refused for good: the agent has
  // read the comment, or the token is not this comment's.
  async function lostToken(c, e) {
    storage.remove(tokenKey(c.n));
    editing = null;
    exitTyping();
    await load();
    const why = lostReason(c.n, e);
    say("comments-status", `${why.charAt(0).toUpperCase()}${why.slice(1)}; it can no longer be changed.`, true);
  }

  // Disables the button while `action` runs; a text button says what is
  // going on, an icon button keeps its icon.
  async function busy(b, label, action) {
    const iconOnly = b.classList.contains("icon");
    const old = b.textContent;
    b.disabled = true;
    b.setAttribute("aria-busy", "true");
    if (!iconOnly) b.textContent = label;
    try {
      await action();
    } finally {
      if (b.isConnected) {
        b.disabled = false;
        b.removeAttribute("aria-busy");
        if (!iconOnly) b.textContent = old;
      }
    }
  }

  function replace(updated) {
    changed(comments.map((c) => (c.n === updated.n ? updated : c)));
  }

  async function resolve(c, b) {
    flushPending();
    const resolving = !c.resolved;
    const back = neighbour(c.n);
    await busy(b, resolving ? "Resolving…" : "Reopening…", async () => {
      try {
        replace(await call("PATCH", `${base}/${c.n}`, { resolved: resolving }));
        render();
        if (resolving) {
          toast(`${named(c.n)} resolved`, { undo: () => reopen(c.n), ms: UNDO_MS, back });
          toastUndo.focus({ preventScroll: true });
        } else {
          toast(`${named(c.n)} reopened`);
        }
      } catch (e) {
        if (e.status === 404) await load();
        say("comments-status", e.status === 404 ? `Comment ${c.n} was deleted.` : e.message, true);
      }
    });
  }

  async function reopen(n) {
    try {
      replace(await call("PATCH", `${base}/${n}`, { resolved: false }));
      light("comment", n);
      render();
      focusBack(`comment-${n}`);
      toast(`${named(n)} reopened`);
    } catch (e) {
      if (e.status === 404) await load();
      toast(`Comment ${n} was not reopened. ${e.status === 404 ? "It was deleted." : e.message}`,
        { error: true, ms: 8000 });
    }
  }

  function startEdit(c) {
    editing = { n: c.n, draft: c.text, start: c.text.length, end: c.text.length };
    render();
    const area = $(`comment-${c.n}`).querySelector("textarea");
    grow(area);
    area.focus();
    area.setSelectionRange(editing.start, editing.end);
  }

  function cancelEdit(n) {
    editing = null;
    exitTyping();
    render();
    const li = $(`comment-${n}`);
    if (li) li.focus();
  }

  async function saveEdit(c, token, b) {
    if (!editing.draft.trim()) {
      say("edit-status", "The text is empty. Write something, or delete the comment.", true);
      return;
    }
    flushPending();
    await busy(b, "Saving…", async () => {
      try {
        replace(await call("PATCH", `${base}/${c.n}`, { edit_token: token, text: editing.draft }));
        editing = null;
        exitTyping();
        render();
        say("comments-status", `Comment ${c.n} saved.`);
        $(`comment-${c.n}`).focus();
      } catch (e) {
        if (e.status === 403 || e.status === 409) return lostToken(c, e);
        if (e.status === 404) {
          editing = null;
          exitTyping();
          await load();
          say("comments-status", `Comment ${c.n} was deleted.`, true);
        } else {
          say("edit-status", e.message, true);
        }
      }
    });
  }

  // Delete hides the comment at once and offers Undo; the DELETE itself goes
  // only when the Undo time is over.
  function remove(c, token) {
    flushPending();
    const back = neighbour(c.n);
    pending = { c, token, timer: setTimeout(flushPending, UNDO_MS) };
    render();
    toast(`${named(c.n)} deleted`, { undo: undoDelete, ms: UNDO_MS, back });
    toastUndo.focus({ preventScroll: true });
  }

  function undoDelete() {
    if (!pending) return;
    clearTimeout(pending.timer);
    const { n } = pending.c;
    pending = null;
    light("comment", n);
    render();
    focusBack(`comment-${n}`);
    toast(`${named(n)} restored`);
  }

  // Sends the waiting delete, if any. The fetch starts synchronously, with
  // keepalive, so this also works from pagehide.
  async function flushPending() {
    if (!pending) return;
    const { c, token, timer } = pending;
    clearTimeout(timer);
    pending = null;
    // Its Undo is over: the toast offering it goes.
    if (undoAction === undoDelete) hideToast();
    deleting.add(c.n);
    let failure = null;
    try {
      await call("DELETE", `${base}/${c.n}`, undefined, { "X-Edit-Token": token }, true);
    } catch (e) {
      if (e.status !== 404) failure = e; // 404: gone already, as wanted
    }
    deleting.delete(c.n);
    if (!failure) {
      storage.remove(tokenKey(c.n));
      changed(comments.filter((x) => x.n !== c.n));
      render();
      return;
    }
    // Not deleted: back in the list, and the person is told why.
    const lost = failure.status === 403 || failure.status === 409;
    if (lost) storage.remove(tokenKey(c.n));
    const message = lost
      ? `Comment ${c.n} was not deleted: ${lostReason(c.n, failure)}.`
      : `Comment ${c.n} was not deleted. ${failure.message}`;
    light("comment", c.n);
    render();
    toast(message, { error: true, ms: 10000 });
    say("comments-status", message, true);
    if (lost) load();
  }

  // Leaving the page sends a waiting delete right away. A switch to another
  // tab does not: the Undo stays possible until its time is over.
  window.addEventListener("pagehide", flushPending);

  // --- The panel, a bottom sheet over a strip on a phone ---------------------
  // Folded, the sheet leaves a one-line strip with the counts. Open, it takes
  // 25 %, 50 % or 100 % of the height: dragging the head or the strip snaps
  // to the nearest (or folds it), a tap on the head steps through them. The
  // height last chosen is kept for the session.

  const SIZES = ["25", "50", "100"];
  const SIZE_KEY = "agent-artifacts:sheet";
  const STRIP_H = 48;
  // A 25 % sheet still shows its head, a comment and the folded form.
  const SHEET_MIN = 200;
  let sheetSize = "25";
  try {
    const kept = sessionStorage.getItem(SIZE_KEY);
    if (SIZES.includes(kept)) sheetSize = kept;
  } catch { /* no storage: 25 % each time */ }
  const head = panel.querySelector(".comments-head");
  const grip = $("comments-grip");

  function chooseSize(size) {
    sheetSize = size;
    try { sessionStorage.setItem(SIZE_KEY, size); } catch { /* kept for this page only */ }
    applySize();
  }

  const stageHeight = () => panel.parentElement.clientHeight;

  function sizePx(size) {
    const stage = stageHeight();
    if (size === "100") return stage;
    const share = size === "50" ? 0.5 : 0.25;
    return Math.min(stage, Math.max(Math.round(window.innerHeight * share), SHEET_MIN));
  }

  function applySize() {
    grip.setAttribute("aria-label", `Sheet height ${sheetSize}%. Change`);
    if (!phone.matches) {
      panel.style.height = "";
      return;
    }
    if (panel.hidden || panel.classList.contains("dragging")) return;
    panel.style.height = `${sizePx(sheetSize)}px`;
    fitPins();
  }

  function setExpanded(open) {
    toggle.setAttribute("aria-expanded", String(open));
    strip.setAttribute("aria-expanded", String(open));
  }

  function openPanel(focus = true) {
    if (mode && phone.matches && !composer.hidden) minimizeComposer();
    panel.hidden = false;
    setExpanded(true);
    syncPicking();
    applySize();
    fitPins();
    dockToast();
    load();
    if (focus) $("comments-title").focus({ preventScroll: true });
  }

  function closePanel() {
    const inside = panel.contains(document.activeElement);
    closeMenu(false);
    if (typingHost && panel.contains(typingHost)) exitTyping();
    panel.hidden = true;
    setExpanded(false);
    syncPicking();
    if (!text.value.trim()) fold(true);
    fitPins();
    if (minimized) restoreComposer();
    else if (inside) (phone.matches ? strip : toggle).focus();
    dockToast();
  }

  // A drag ends in a click on some browsers; that click must not also open
  // the sheet or step its height.
  let draggedAt = -Infinity;
  const justDragged = () => performance.now() - draggedAt < 400;

  toggle.addEventListener("click", () => (panel.hidden ? openPanel() : closePanel()));
  strip.addEventListener("click", () => { if (!justDragged()) openPanel(); });
  $("comments-close").addEventListener("click", closePanel);
  // A tap on the head (the grip, the title) steps 25 → 50 → 100 → 25 %.
  head.addEventListener("click", (e) => {
    if (!phone.matches || justDragged() || e.target.closest("button:not(.grip)")) return;
    chooseSize(SIZES[(SIZES.indexOf(sheetSize) + 1) % SIZES.length]);
  });
  showResolved.addEventListener("click", () => {
    resolvedShown = !resolvedShown;
    render();
    if (resolvedShown) resolvedList.scrollIntoView({ block: "nearest" });
  });
  window.addEventListener("resize", () => { applySize(); dockToast(); });
  phone.addEventListener("change", () => { closeMenu(false); applySize(); dockToast(); });

  // Dragging the head or the strip: the sheet follows the finger, then
  // snaps, preferring a height in the direction of the drag.
  let drag = null;

  function startDrag(e) {
    if (!phone.matches || e.button > 0 || drag) return;
    if (e.target.closest("button:not(.grip):not(.strip)")) return;
    drag = {
      id: e.pointerId,
      y: e.clientY,
      from: panel.hidden ? STRIP_H : panel.offsetHeight,
      closed: panel.hidden,
      moved: false,
    };
    window.addEventListener("pointermove", moveDrag);
    window.addEventListener("pointerup", endDrag);
    window.addEventListener("pointercancel", endDrag);
  }

  function moveDrag(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const dy = e.clientY - drag.y;
    if (!drag.moved) {
      if (Math.abs(dy) < 8) return;
      drag.moved = true;
      panel.classList.add("dragging");
      if (panel.hidden) openPanel(false);
    }
    panel.style.height = `${Math.min(stageHeight(), Math.max(STRIP_H, drag.from - dy))}px`;
  }

  function endDrag(e) {
    if (!drag || e.pointerId !== drag.id) return;
    window.removeEventListener("pointermove", moveDrag);
    window.removeEventListener("pointerup", endDrag);
    window.removeEventListener("pointercancel", endDrag);
    const { moved, y, closed } = drag;
    drag = null;
    if (!moved) return; // a tap: the click handlers above take it
    draggedAt = performance.now();
    panel.classList.remove("dragging");
    const height = panel.offsetHeight;
    const up = e.clientY < y;
    let best = null;
    let distance = Infinity;
    // Pulled up from the strip, the sheet opens, however short the pull.
    for (const size of closed && up ? SIZES : ["closed", ...SIZES]) {
      const h = size === "closed" ? STRIP_H : sizePx(size);
      const ahead = up ? h > height : h < height;
      const d = Math.abs(h - height) - (ahead ? 40 : 0);
      if (d < distance) { distance = d; best = size; }
    }
    if (best === "closed") closePanel();
    else chooseSize(best);
  }

  head.addEventListener("pointerdown", startDrag);
  strip.addEventListener("pointerdown", startDrag);

  // --- A comment's actions: a sheet on a phone, a menu on a computer ---------

  const menu = $("card-menu");
  const menuList = $("card-menu-list");
  // {n, opener}: the comment the menu is for and where focus goes back.
  let menuFor = null;
  // A long press opens the menu under the finger: the release of that same
  // press must not pick an item. Only a press that starts in the open menu,
  // or a keyboard, does.
  let menuArmed = true;

  function menuItems(c) {
    const token = storage.get(tokenKey(c.n));
    const card = () => $(`comment-${c.n}`);
    // The card's ⋯ button stands for the busy state of a resolve.
    const busyButton = () => (card() && card().querySelector(".more")) || el("button");
    const items = [c.resolved
      ? { icon: "reopen", label: "Reopen", run: () => resolve(c, busyButton()) }
      : { icon: "check", label: "Resolve", run: () => resolve(c, busyButton()) }];
    if (token && !c.read) {
      // Not while it is being edited: that would start over and drop the draft.
      if (!(editing && editing.n === c.n)) items.push({ icon: "edit", label: "Edit", run: () => startEdit(c) });
      items.push({ icon: "delete", label: "Delete", danger: true, run: () => remove(c, token) });
    }
    // Comments cannot be selected on a phone (a long press opens this menu).
    items.push({ icon: "copy", label: "Copy text", run: () => copyText(c.n) });
    items.push({ icon: "link", label: "Copy link", run: () => copyLink(c.n) });
    return items;
  }

  // `point` places the menu at the pointer (a right click); without it, on
  // a phone, the menu is a sheet at the bottom.
  function openMenu(c, opener, { point = null, armed = true } = {}) {
    closeMenu(false);
    // A menu sheet is for a phone; elsewhere the ⋯ button opens it under itself.
    if (!point && !phone.matches && opener) {
      const r = opener.getBoundingClientRect();
      point = { x: r.left, y: r.bottom + 4 };
    }
    menuFor = { n: c.n, opener };
    if (opener && opener.matches(".more")) opener.setAttribute("aria-expanded", "true");
    menuArmed = armed;
    $("card-menu-title").textContent = `#${c.n} · ${c.author}: ${c.text}`;
    menuList.replaceChildren(...menuItems(c).map((item) => {
      const b = el("button", item.danger ? "danger" : "");
      b.type = "button";
      b.setAttribute("role", "menuitem");
      b.append(icon(item.icon), el("span", "", item.label));
      b.addEventListener("click", (e) => {
        if (!menuArmed && e.detail !== 0) return;
        closeMenu();
        item.run();
      });
      return b;
    }));
    const card = $(`comment-${c.n}`);
    if (card) card.classList.add("pressed");
    menu.classList.toggle("popover", Boolean(point));
    const box = menu.querySelector(".menu-box");
    box.style.left = "";
    box.style.top = "";
    menu.hidden = false;
    if (point) {
      const w = box.offsetWidth;
      const h = box.offsetHeight;
      const vw = document.documentElement.clientWidth;
      const vh = window.innerHeight;
      box.style.left = `${Math.max(8, Math.min(point.x, vw - w - 8))}px`;
      box.style.top = `${Math.max(8, point.y + h > vh - 8 ? point.y - h : point.y)}px`;
    }
    menuList.firstChild.focus({ preventScroll: true });
  }

  // Closes the menu; focus goes back to what opened it unless `restore` is false.
  function closeMenu(restore = true) {
    if (menu.hidden) return;
    menu.hidden = true;
    const { n, opener } = menuFor;
    menuFor = null;
    const card = $(`comment-${n}`);
    if (card) card.classList.remove("pressed");
    // Focus first: a computer's ⋯ shows only while expanded, hovered or focused.
    if (restore && opener && opener.isConnected) opener.focus({ preventScroll: true });
    else if (restore && card) card.focus({ preventScroll: true });
    if (opener && opener.matches(".more")) opener.setAttribute("aria-expanded", "false");
  }

  menu.addEventListener("pointerdown", () => { menuArmed = true; });
  // A right click elsewhere with the menu open moves the menu there.
  menu.addEventListener("contextmenu", (e) => {
    e.preventDefault();
    if (!e.target.closest(".menu-scrim")) return;
    // The browser's own long-press menu event on a phone: keep our sheet.
    if (phone.matches && touch.matches) return;
    closeMenu(false);
    const under = document.elementFromPoint(e.clientX, e.clientY);
    const hit = under && listBox.contains(under) ? cardOf(under) : null;
    if (hit) openMenu(hit.c, hit.li, { point: { x: e.clientX, y: e.clientY } });
  });
  menu.querySelector(".menu-scrim").addEventListener("click", (e) => {
    if (menuArmed || e.detail === 0) closeMenu();
  });
  menu.addEventListener("keydown", (e) => {
    const items = [...menuList.children];
    const at = items.indexOf(document.activeElement);
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const step = e.key === "ArrowDown" ? 1 : -1;
      items[(at + step + items.length) % items.length].focus();
    } else if (e.key === "Tab") {
      e.preventDefault();
      closeMenu();
    }
  });
  window.addEventListener("resize", () => closeMenu(false));

  // Copies `value`; true if it worked. Focus ends on the comment `n`.
  async function copy(value, n) {
    try {
      await navigator.clipboard.writeText(value);
      return true;
    } catch {
      // No clipboard access (an insecure page, a frame): the old way.
      const area = el("textarea", "copy-source");
      area.value = value;
      area.readOnly = true;
      document.body.append(area);
      area.select();
      let copied = false;
      try { copied = document.execCommand("copy"); } catch { /* not allowed */ }
      area.remove();
      focusBack(`comment-${n}`);
      return copied;
    }
  }

  // The link of the page with the comment's number; opening it shows the comment.
  async function copyLink(n) {
    const link = `${$("url").value.split("#")[0]}#comment-${n}`;
    if (await copy(link, n)) toast(`Link to ${named(n)} copied`);
    else toast(`Could not copy. The link: ${link}`, { error: true, ms: 10000 });
  }

  async function copyText(n) {
    const c = comments.find((x) => x.n === n);
    if (!c) return;
    if (await copy(c.text, n)) toast("Copied");
    else toast(`Could not copy the text of ${named(n)}.`, { error: true, ms: 8000 });
  }

  // A long press on a comment (a phone) or a right click (a computer).
  const listBox = panel.querySelector(".comments-body");
  const cardOf = (target) => {
    const li = target.closest(".comment");
    if (!li || target.closest("button, textarea, input, a, .comment-edit")) return null;
    const c = comments.find((x) => `comment-${x.n}` === li.id);
    return c ? { li, c } : null;
  };
  let press = null;
  let pressedAt = -Infinity;

  function endPress() {
    if (!press) return;
    clearTimeout(press.timer);
    if (press.fired) pressedAt = performance.now();
    press = null;
  }

  listBox.addEventListener("pointerdown", (e) => {
    endPress();
    pressedAt = -Infinity; // a new press: its click is a real one
    if (!phone.matches || e.button > 0) return;
    const hit = cardOf(e.target);
    if (!hit) return;
    press = { id: e.pointerId, x: e.clientX, y: e.clientY, fired: false };
    press.timer = setTimeout(() => {
      press.fired = true;
      try { if (navigator.vibrate) navigator.vibrate(10); } catch { /* not allowed */ }
      openMenu(hit.c, hit.li, { armed: false });
    }, 450);
  });
  listBox.addEventListener("pointermove", (e) => {
    if (press && !press.fired && e.pointerId === press.id
      && Math.hypot(e.clientX - press.x, e.clientY - press.y) > 10) endPress();
  });
  for (const type of ["pointerup", "pointercancel"]) {
    window.addEventListener(type, (e) => { if (press && e.pointerId === press.id) endPress(); });
  }
  listBox.addEventListener("scroll", endPress, { passive: true });
  // The release of a long press is not a tap on the comment.
  listBox.addEventListener("click", (e) => {
    if (performance.now() - pressedAt < 600) {
      pressedAt = -Infinity;
      e.preventDefault();
      e.stopPropagation();
    }
  }, true);
  listBox.addEventListener("contextmenu", (e) => {
    const hit = cardOf(e.target);
    if (!hit) return;
    // A phone's own long-press menu would cover ours; a long press there
    // opens the sheet (above). A mouse, in a window of any width, gets the
    // menu at the pointer.
    e.preventDefault();
    if (!phone.matches || !touch.matches) openMenu(hit.c, hit.li, { point: { x: e.clientX, y: e.clientY } });
  });

  // --- Typing mode on a phone ------------------------------------------------
  // While a comment field has the focus on a touch screen, the header, the
  // content and the sheet step aside: the form being typed in sits at the
  // top, under a line saying what is written and, for a comment with a pin,
  // a strip of the image around it. The keyboard covers only the bottom, in
  // a frame as well as at the top level, with no viewport arithmetic. Leaving
  // the field brings everything back, the typed text kept.

  const typingContext = $("typing-context");
  const typingStrip = $("typing-strip");
  const typingImage = $("typing-image");
  const STRIP_PX = 96;
  let typingHost = null;
  // The visual viewport's height when typing mode began, and whether an
  // on-screen keyboard has taken part of it since.
  let typingBase = null;

  const hostOf = (node) => (node && node.matches && node.matches("input, textarea")
    ? node.closest("#composer, #comment-form, .comment-edit")
    : null);

  function typingAnchor(host) {
    if (mode === "element") return null;
    if (host === composer) return draft;
    if (host === form || !mode) return null;
    const c = comments.find((x) => x.n === Number(host.dataset.n));
    return c && c.anchor && c.anchor.type === "point" ? c.anchor : null;
  }

  // The image around the spot, at about its own size, the pin in the middle
  // unless the edge of the image is nearer.
  function fillStrip(anchor) {
    typingStrip.hidden = !anchor || !image || !image.naturalWidth;
    if (typingStrip.hidden) return;
    typingImage.src = image.currentSrc || image.src;
    const sw = typingStrip.clientWidth;
    const nw = image.naturalWidth;
    const nh = image.naturalHeight;
    let w = Math.min(Math.max(nw, sw * 1.5), sw * 2);
    if ((w * nh) / nw < STRIP_PX) w = (STRIP_PX * nw) / nh;
    const h = (w * nh) / nw;
    const place = (size, room, at) => (size <= room
      ? (room - size) / 2
      : Math.min(0, Math.max(room - size, room / 2 - at * size)));
    const left = place(w, sw, anchor.x);
    const top = place(h, STRIP_PX, anchor.y);
    typingImage.style.width = `${w}px`;
    typingImage.style.height = `${h}px`;
    typingImage.style.left = `${left}px`;
    typingImage.style.top = `${top}px`;
    const pin = typingStrip.querySelector(".typing-pin");
    pin.style.left = `${left + anchor.x * w}px`;
    pin.style.top = `${top + anchor.y * h}px`;
  }


  function enterTyping(host) {
    if (typingHost === host) return;
    // A redrawn editor taking the old one's place keeps the measurement.
    const base = typingHost ? typingBase : window.visualViewport
      && { height: window.visualViewport.height, keyboard: false };
    if (typingHost) clearTyping();
    typingBase = base || null;
    typingHost = host;
    closeMenu(false);
    host.classList.add("typing-host");
    // Everything off the way from the page to the form is hidden.
    for (let node = host.parentElement; node && node !== document.body; node = node.parentElement) {
      node.classList.add("typing-path");
    }
    document.body.classList.add("typing");
    if (mode === "point") imageBox.style.paddingBottom = "";
    $("typing-title").textContent = host === composer
      ? (mode === "point" ? "New comment on the image" : $("composer-on").textContent)
      : host === form ? "New comment on the whole file" : `Editing #${host.dataset.n}`;
    host.prepend(typingContext);
    typingContext.hidden = false;
    fillStrip(typingAnchor(host));
    const area = host.querySelector("textarea");
    if (area) grow(area);
    dockToast();
  }

  function clearTyping() {
    typingHost.classList.remove("typing-host");
    for (const node of document.querySelectorAll(".typing-path")) node.classList.remove("typing-path");
    document.body.classList.remove("typing");
    typingContext.hidden = true;
    document.body.append(typingContext);
    const area = typingHost.querySelector("textarea");
    typingHost = null;
    typingBase = null;
    if (area) grow(area);
  }

  function exitTyping() {
    if (!typingHost) return;
    clearTyping();
    applySize();
    fitPins();
    placeComposer();
    dockToast();
    if (mode && !composer.hidden) requestAnimationFrame(() => centerOn(null));
  }

  // Cancel in typing mode: back to the page, focus on the form or comment.
  function leaveTyping() {
    const host = typingHost;
    if (!host) return;
    exitTyping();
    // The form left empty folds back to its one button.
    if (host === form && !text.value.trim()) {
      fold(true);
      $("comment-open").focus({ preventScroll: true });
      return;
    }
    const target = host.matches(".comment-edit") ? host.closest(".comment") : host;
    if (!target) return;
    target.focus({ preventScroll: true });
    target.scrollIntoView({ block: "nearest" });
  }

  document.addEventListener("focusin", (e) => {
    const host = hostOf(e.target);
    if (host && phone.matches && touch.matches) enterTyping(host);
  });
  // A moment later, so a tap on Send or Cancel lands before the layout moves.
  document.addEventListener("focusout", () => {
    if (!typingHost) return;
    setTimeout(() => {
      if (typingHost && !hostOf(document.activeElement)) exitTyping();
    }, 150);
  });
  // The field keeps the focus (and the keyboard stays up) while a button of
  // the form is pressed.
  document.addEventListener("pointerdown", (e) => {
    if (typingHost && typingHost.contains(e.target) && e.target.closest("button")) e.preventDefault();
  });
  document.addEventListener("input", (e) => {
    if (!e.target.matches("#composer-text, #comment-text, .comment-edit textarea")) return;
    grow(e.target);
    if (typingHost) dockToast();
    else placeComposer();
  });
  phone.addEventListener("change", () => { if (!phone.matches) exitTyping(); });
  // Android hides the keyboard (the Back button) but leaves the field
  // focused: once the viewport is back to nearly its height from before the
  // keyboard, typing mode ends as on a blur, the text kept.
  if (window.visualViewport) {
    window.visualViewport.addEventListener("resize", () => {
      // A pinch zoom changes the height too; only the keyboard counts.
      if (!typingHost || !typingBase || window.visualViewport.scale !== 1) return;
      const height = window.visualViewport.height;
      if (height < typingBase.height * 0.9) typingBase.keyboard = true;
      else if (typingBase.keyboard) leaveTyping();
    });
  }

  function escape() {
    if (!menu.hidden) closeMenu();
    else if (mode && !composer.hidden) cancelComposer();
    else if (!panel.hidden) closePanel();
  }
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") escape(); });
  // Others may have commented while this tab was in the background.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") load();
  });

  if (/Mac|iPhone|iPad/.test(navigator.platform)) {
    for (const hint of document.querySelectorAll(".hint")) hint.textContent = "Cmd+Enter to send";
  }
  author.value = storage.get(NAME_KEY) || "";
  load();
})();
