// Comments on the viewer page: the header button, the panel (a bottom sheet
// on a phone) with the list and a form for the whole file, the composer at a
// spot clicked on an image, pins, and the toasts with Undo. Names and texts
// come from anyone with the link, so they go in as text nodes only, never as
// HTML.
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
  const toastBox = $("toast");
  const toastText = $("toast-text");
  const toastUndo = $("toast-undo");

  // How a comment is tied to the content of this kind of file: "point" on an
  // image, null for a comment about the whole file. Pins on elements of
  // pages (html, md, txt) need the script inside the sandbox and come later.
  function pinMode(kind) {
    return ["png", "jpeg", "gif", "webp"].includes(kind) && image && composer ? "point" : null;
  }
  const mode = pinMode(panel.dataset.kind);

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

  // A toast over the page, with an Undo button when `undo` is given.
  let undoAction = null;
  function toast(message, { undo = null, ms = 4000, error = false } = {}) {
    clearTimeout(timers.toast);
    toastText.textContent = message;
    toastBox.classList.toggle("error", error);
    undoAction = undo;
    toastUndo.hidden = !undo;
    toastBox.classList.add("on");
    timers.toast = setTimeout(hideToast, ms);
  }

  function hideToast() {
    clearTimeout(timers.toast);
    const focused = toastBox.contains(document.activeElement);
    toastBox.classList.remove("on");
    undoAction = null;
    toastUndo.hidden = true;
    if (focused) (panel.hidden ? (phone.matches ? strip : toggle) : $("comments-title")).focus();
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

  function button(label, onClick, className = "text") {
    const b = el("button", className, label);
    b.type = "button";
    b.addEventListener("click", onClick);
    return b;
  }

  function checkIcon() {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", "m5 12 5 5 9-10");
    svg.append(path);
    return svg;
  }

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
  const hasPin = (c) => mode && c.anchor && c.anchor.type === "point" && (!c.resolved || resolvedShown);
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
    stripText.textContent = `Comments · ${open.length} open`;
    $("comments-empty").hidden = shown.length > 0;
    if (!resolved.length) resolvedShown = false;
    list.replaceChildren(...open.map(item));
    resolvedList.replaceChildren(...resolved.map(item));
    showResolved.hidden = resolved.length === 0;
    showResolved.textContent = `${resolvedShown ? "Hide" : "Show"} resolved (${resolved.length})`;
    showResolved.setAttribute("aria-expanded", String(resolvedShown));
    resolvedList.hidden = !resolvedShown;
    drawPins();
    if (editing && editorFocused) {
      const area = $(`comment-${editing.n}`).querySelector("textarea");
      area.focus({ preventScroll: true });
      area.setSelectionRange(editing.start, editing.end);
    } else if (focusedItem && $(focusedItem)) {
      $(focusedItem).focus({ preventScroll: true });
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
      badge.append(checkIcon());
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
    li.append(meta);

    if (editing && editing.n === c.n) {
      li.append(editor(c, token));
      return li;
    }
    li.append(el("p", "comment-text", c.text));
    const tools = el("div", "comment-tools");
    tools.append(button(c.resolved ? "Reopen" : "Resolve", (e) => resolve(c, e.target)));
    if (token && !c.read) {
      tools.append(button("Edit", () => startEdit(c)));
      tools.append(button("Delete", () => remove(c, token)));
    }
    li.append(tools);
    return li;
  }

  function editor(c, token) {
    const box = el("div", "comment-edit");
    const area = el("textarea");
    area.value = editing.draft;
    area.maxLength = 2000;
    area.rows = 3;
    area.setAttribute("aria-label", `Text of comment ${c.n}`);
    area.addEventListener("input", () => { editing.draft = area.value; });
    const save = button("Save", () => saveEdit(c, token, save));
    area.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); save.click(); }
      if (e.key === "Escape") { e.stopPropagation(); cancelEdit(c.n); }
    });
    const tools = el("div", "comment-tools");
    tools.append(save, button("Cancel", () => cancelEdit(c.n)));
    box.append(area, tools);
    return box;
  }

  function drawPins() {
    if (!mode) return;
    const shown = visible().filter(hasPin).map((c) => {
      const pin = pinAt(c.anchor, String(c.n));
      pin.dataset.n = c.n;
      if (c.resolved) pin.classList.add("resolved");
      if (lit.pin === c.n) pin.classList.add("lit");
      pin.setAttribute("aria-label", `Comment ${c.n}${c.resolved ? ", resolved" : ""}`);
      pin.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        showComment(c.n);
      });
      return pin;
    });
    if (draft) {
      // Unnumbered: the server hands out the number on sending.
      const pin = pinAt(draft, "+");
      pin.classList.add("new");
      pin.setAttribute("aria-label", "The spot of the comment you are writing");
      pin.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        $("composer-text").focus();
      });
      shown.push(pin);
    }
    pins.replaceChildren(...shown);
    pins.hidden = shown.length === 0;
    fitPins();
  }

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
    if (!mode) return;
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
    }, 2500);
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
    centerPin(pins.querySelector(`[data-n="${n}"]`));
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

  // --- The composer at a spot on the image ----------------------------------

  function placeComposer() {
    if (!mode || composer.hidden) return;
    if (phone.matches) {
      // Docked to the bottom of the visual viewport: the top of the keyboard.
      composer.classList.add("dock");
      composer.style.left = "";
      const vv = window.visualViewport;
      const bottom = vv ? vv.offsetTop + vv.height : window.innerHeight;
      const top = bottom - composer.offsetHeight;
      composer.style.top = `${top}px`;
      // Room under the image, so the pin can scroll up above the composer.
      const covered = imageBox.getBoundingClientRect().bottom - top;
      imageBox.style.paddingBottom = covered > 0 ? `${covered + 8}px` : "";
      // Toasts show above the composer, not over its buttons.
      toastBox.style.bottom = `${window.innerHeight - top + 8}px`;
      return;
    }
    toastBox.style.bottom = "";
    // Next to the pin, inside the window: below it, or above with no room.
    composer.classList.remove("dock");
    imageBox.style.paddingBottom = "";
    const img = image.getBoundingClientRect();
    const box = imageBox.getBoundingClientRect();
    const clamp = (v, lo, hi) => Math.min(Math.max(v, lo), hi);
    const x = clamp(img.left + draft.x * img.width, box.left, box.right);
    const y = clamp(img.top + draft.y * img.height, box.top, box.bottom);
    const w = composer.offsetWidth;
    const h = composer.offsetHeight;
    const vw = document.documentElement.clientWidth;
    const vh = window.innerHeight;
    let top = y + 12;
    if (top + h > vh - 12) top = y - h - 36; // the pin itself stands above the spot
    composer.style.left = `${clamp(x - w / 2, 12, vw - w - 12)}px`;
    composer.style.top = `${clamp(top, 12, Math.max(12, vh - h - 12))}px`;
  }

  function openComposer(anchor) {
    const wasOpen = !composer.hidden;
    draft = anchor;
    lit.pin = null;
    if (!wasOpen) {
      // On a phone the composer takes the bottom; the sheet folds to the strip.
      if (phone.matches && !panel.hidden) closePanel();
      const name = $("composer-author");
      if (!name.value) name.value = storage.get(NAME_KEY) || "";
      say("composer-status", "");
      composer.hidden = false;
    }
    drawPins();
    placeComposer();
    $("composer-text").focus({ preventScroll: true });
    if (phone.matches) {
      // Again once the keyboard is up, which resizes the visual viewport.
      requestAnimationFrame(() => centerPin(pins.querySelector(".pin.new")));
    }
  }

  function closeComposer() {
    const inside = composer.contains(document.activeElement);
    composer.hidden = true;
    if (inside) $("image-link").focus({ preventScroll: true });
    draft = null;
    $("composer-text").value = "";
    imageBox.style.paddingBottom = "";
    toastBox.style.bottom = "";
    say("composer-status", "");
    drawPins();
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
    $("composer-cancel").addEventListener("click", closeComposer);

    // A plain click places the spot (or moves it while writing); a Ctrl/Cmd,
    // Shift or middle click still opens the image full size.
    $("image-link").addEventListener("click", (e) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
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
    if (window.ResizeObserver) new ResizeObserver(() => { fitPins(); placeComposer(); }).observe(image);
    window.addEventListener("resize", () => { fitPins(); placeComposer(); });
    imageBox.addEventListener("scroll", () => { if (!phone.matches) placeComposer(); });
    phone.addEventListener("change", placeComposer);
    if (window.visualViewport) {
      window.visualViewport.addEventListener("resize", () => {
        placeComposer();
        if (!composer.hidden && phone.matches) centerPin(pins.querySelector(".pin.new"));
      });
      window.visualViewport.addEventListener("scroll", placeComposer);
    }
    $("form-note").hidden = false;
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

  // --- Actions --------------------------------------------------------------

  const lostReason = (n, e) => (e.code === "already_read"
    ? `the agent has already read comment ${n}`
    : `comment ${n} cannot be changed from this browser`);

  // A token-holder's edit that the server refused for good: the agent has
  // read the comment, or the token is not this comment's.
  async function lostToken(c, e) {
    storage.remove(tokenKey(c.n));
    editing = null;
    await load();
    const why = lostReason(c.n, e);
    say("comments-status", `${why.charAt(0).toUpperCase()}${why.slice(1)}; it can no longer be changed.`, true);
  }

  async function busy(b, label, action) {
    const old = b.textContent;
    b.disabled = true;
    b.textContent = label;
    try {
      await action();
    } finally {
      if (b.isConnected) { b.disabled = false; b.textContent = old; }
    }
  }

  function replace(updated) {
    changed(comments.map((c) => (c.n === updated.n ? updated : c)));
  }

  async function resolve(c, b) {
    flushPending();
    const resolving = !c.resolved;
    await busy(b, resolving ? "Resolving…" : "Reopening…", async () => {
      try {
        replace(await call("PATCH", `${base}/${c.n}`, { resolved: resolving }));
        render();
        if (resolving) {
          toast(`Comment ${c.n} resolved`, { undo: () => reopen(c.n), ms: UNDO_MS });
          toastUndo.focus({ preventScroll: true });
        } else {
          toast(`Comment ${c.n} reopened.`);
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
      toast(`Comment ${n} reopened.`);
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
    area.focus();
    area.setSelectionRange(editing.start, editing.end);
  }

  function cancelEdit(n) {
    editing = null;
    render();
    const li = $(`comment-${n}`);
    if (li) li.focus();
  }

  async function saveEdit(c, token, b) {
    if (!editing.draft.trim()) {
      say("comments-status", "The text is empty. Write something, or delete the comment.", true);
      return;
    }
    flushPending();
    await busy(b, "Saving…", async () => {
      try {
        replace(await call("PATCH", `${base}/${c.n}`, { edit_token: token, text: editing.draft }));
        editing = null;
        render();
        say("comments-status", `Comment ${c.n} saved.`);
        $(`comment-${c.n}`).focus();
      } catch (e) {
        if (e.status === 403 || e.status === 409) return lostToken(c, e);
        if (e.status === 404) { editing = null; await load(); }
        say("comments-status", e.status === 404 ? `Comment ${c.n} was deleted.` : e.message, true);
      }
    });
  }

  // Delete hides the comment at once and offers Undo; the DELETE itself goes
  // only when the Undo time is over.
  function remove(c, token) {
    flushPending();
    pending = { c, token, timer: setTimeout(flushPending, UNDO_MS) };
    render();
    toast(`Comment ${c.n} deleted`, { undo: undoDelete, ms: UNDO_MS });
    toastUndo.focus({ preventScroll: true });
  }

  function undoDelete() {
    if (!pending) return;
    clearTimeout(pending.timer);
    const { n } = pending.c;
    pending = null;
    light("comment", n);
    render();
    toast(`Comment ${n} restored.`);
  }

  // Sends the waiting delete, if any. The fetch starts synchronously, with
  // keepalive, so this also works from pagehide.
  async function flushPending() {
    if (!pending) return;
    const { c, token, timer } = pending;
    clearTimeout(timer);
    pending = null;
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

  // Leaving the page or the tab sends a waiting delete right away.
  window.addEventListener("pagehide", flushPending);

  // --- The panel, a bottom sheet with a strip on a phone ---------------------

  function setExpanded(open) {
    toggle.setAttribute("aria-expanded", String(open));
    strip.setAttribute("aria-expanded", String(open));
  }

  function openPanel(focus = true) {
    panel.hidden = false;
    setExpanded(true);
    fitPins();
    load();
    if (focus) $("comments-title").focus();
  }

  function closePanel() {
    const inside = panel.contains(document.activeElement);
    panel.hidden = true;
    panel.style.transform = "";
    setExpanded(false);
    if (!text.value.trim()) fold(true);
    fitPins();
    if (inside) (phone.matches ? strip : toggle).focus();
  }

  toggle.addEventListener("click", () => (panel.hidden ? openPanel() : closePanel()));
  strip.addEventListener("click", () => openPanel());
  $("comments-close").addEventListener("click", closePanel);
  showResolved.addEventListener("click", () => {
    resolvedShown = !resolvedShown;
    render();
  });

  // A swipe down on the sheet's grip or head folds it to the strip.
  let swipeFrom = null;
  for (const handle of [panel.querySelector(".grip"), panel.querySelector(".comments-head")]) {
    handle.addEventListener("touchstart", (e) => {
      if (!phone.matches) return;
      swipeFrom = e.touches[0].clientY;
      panel.classList.add("dragging");
    }, { passive: true });
    handle.addEventListener("touchmove", (e) => {
      if (swipeFrom === null) return;
      const d = Math.max(0, e.touches[0].clientY - swipeFrom);
      panel.style.transform = d ? `translateY(${d}px)` : "";
    }, { passive: true });
    const end = (e) => {
      if (swipeFrom === null) return;
      const d = e.changedTouches.length ? e.changedTouches[0].clientY - swipeFrom : 0;
      swipeFrom = null;
      panel.classList.remove("dragging");
      panel.style.transform = "";
      if (d > 60) closePanel();
    };
    handle.addEventListener("touchend", end);
    handle.addEventListener("touchcancel", end);
  }

  document.addEventListener("keydown", (e) => {
    if (e.key !== "Escape") return;
    if (mode && !composer.hidden) closeComposer();
    else if (!panel.hidden) closePanel();
  });
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "hidden") flushPending();
    // Others may have commented while this tab was in the background.
    else load();
  });

  if (/Mac|iPhone|iPad/.test(navigator.platform)) {
    for (const hint of document.querySelectorAll(".hint")) hint.textContent = "Cmd+Enter to send";
  }
  author.value = storage.get(NAME_KEY) || "";
  load();
})();
