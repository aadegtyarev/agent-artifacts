// Comments on the viewer page: the header button, the panel with the list and
// the form, and pins on images. Names and texts come from anyone with the
// link, so they go in as text nodes only, never as HTML.
(() => {
  const panel = document.getElementById("comments");
  if (!panel) return;
  const $ = (id) => document.getElementById(id);
  const artifact = panel.dataset.artifact;
  const base = `/a/${artifact}/comments`;
  const toggle = $("comments-toggle");
  const count = $("comments-count");
  const list = $("comment-list");
  const resolvedList = $("resolved-list");
  const showResolved = $("show-resolved");
  const form = $("comment-form");
  const author = $("comment-author");
  const text = $("comment-text");
  const send = $("comment-send");
  const image = $("image");
  const pins = $("pins");

  // How a comment is tied to the content of this kind of file: "point" on an
  // image, null for a comment about the whole file. Pins on elements of
  // pages (html, md, txt) need the script inside the sandbox and come later.
  function pinMode(kind) {
    return ["png", "jpeg", "gif", "webp"].includes(kind) && image ? "point" : null;
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
  // The spot of the comment being written: "waiting" for a click on the
  // image, "placed" with the anchor in `spot`, or "whole" for the whole file.
  let spotState = mode ? "waiting" : "whole";
  let spot = null;
  // {n, draft, start, end} while a comment's text is being edited; kept
  // here so a redraw of the list does not lose the text or the caret.
  let editing = null;
  // The comment and the pin highlighted for a moment, redrawn lit.
  const lit = { comment: null, pin: null };
  // Every change to `comments` bumps it, so an older GET cannot overwrite it.
  let generation = 0;
  const timers = {};
  const phone = window.matchMedia("(max-width: 640px)");

  class ApiError extends Error {
    constructor(status, code, message) {
      super(message);
      this.status = status;
      this.code = code;
    }
  }

  async function call(method, url, body, headers = {}) {
    const init = { method, headers: { ...headers }, cache: "no-store" };
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

  // A message in the panel (`where` = "comments-status") or under the form
  // ("form-status"); a success fades after a few seconds, an error stays.
  function say(where, message, error = false) {
    const el = $(where);
    clearTimeout(timers[where]);
    el.textContent = message;
    el.classList.toggle("error", error);
    if (message && !error) timers[where] = setTimeout(() => { el.textContent = ""; }, 4000);
  }

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

  const isPinned = (c) => !c.resolved && c.anchor && c.anchor.type === "point";

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
    if (editing && !comments.some((c) => c.n === editing.n)) {
      say("comments-status", `Comment ${editing.n} was deleted.`, true);
      editing = null;
    }
    const open = comments.filter((c) => !c.resolved);
    const resolved = comments.filter((c) => c.resolved);
    count.textContent = open.length;
    toggle.setAttribute("aria-label", `Comments, ${open.length} open`);
    $("comments-empty").hidden = comments.length > 0;
    list.replaceChildren(...open.map(item));
    resolvedList.replaceChildren(...resolved.map(item));
    showResolved.hidden = resolved.length === 0;
    if (!resolved.length) resolvedShown = false;
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

    const meta = el("div", "comment-meta");
    meta.append(isPinned(c)
      ? button(String(c.n), () => highlightPin(c.n), "badge")
      : el("span", "number", `#${c.n}`));
    if (isPinned(c)) meta.firstChild.setAttribute("aria-label", `Show the pin of comment ${c.n}`);
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
      tools.append(button("Delete", (e) => remove(c, token, e.target)));
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
    const shown = comments.filter(isPinned).map((c) => {
      const pin = pinAt(c.anchor, String(c.n));
      pin.dataset.n = c.n;
      if (lit.pin === c.n) pin.classList.add("lit");
      pin.setAttribute("aria-label", `Comment ${c.n}`);
      pin.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        highlightComment(c.n);
      });
      return pin;
    });
    if (spotState === "placed") {
      // Unnumbered: the server hands out the number on sending.
      const pin = pinAt(spot, "+");
      pin.classList.add("new");
      pin.setAttribute("aria-label", "The spot of the comment you are writing");
      pin.addEventListener("click", (e) => { e.preventDefault(); e.stopPropagation(); text.focus(); });
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

  function highlightComment(n) {
    if (panel.hidden) openPanel(false);
    const c = comments.find((x) => x.n === n);
    if (c && c.resolved) resolvedShown = true;
    light("comment", n);
    render();
    const li = $(`comment-${n}`);
    if (!li) return;
    li.scrollIntoView({ block: "nearest" });
    li.focus({ preventScroll: true });
  }

  function highlightPin(n) {
    light("pin", n);
    drawPins();
    const pin = pins.querySelector(`[data-n="${n}"]`);
    if (pin) pin.scrollIntoView({ block: "center", inline: "center" });
  }

  // --- The spot of a new comment --------------------------------------------

  function pinModeOn() {
    return mode && !panel.hidden && spotState !== "whole" && !(phone.matches && folded());
  }

  // --- The form, folded to one button on a phone until it is needed ---------

  const folded = () => form.classList.contains("folded");

  function fold(yes) {
    form.classList.toggle("folded", yes);
    showSpot();
  }

  $("comment-open").addEventListener("click", () => {
    fold(false);
    text.focus();
  });
  // Folds again when left empty, unless a spot is still to be clicked.
  form.addEventListener("focusout", () => {
    setTimeout(() => {
      if (form.contains(document.activeElement) || text.value.trim()) return;
      if (spotState === "whole") fold(true);
    }, 0);
  });
  phone.addEventListener("change", showSpot);

  function showSpot() {
    $("spot").hidden = !mode;
    if (!mode) return;
    $("image-box").classList.toggle("waiting-spot", pinModeOn());
    const state = {
      waiting: ["Click a spot on the image for this comment.", "Whole file instead"],
      placed: ["Pin placed. Click the image again to move it.", "Whole file instead"],
      whole: ["This comment is about the whole file.", "Pick a spot"],
    }[spotState];
    $("spot").classList.toggle("waiting", spotState === "waiting");
    $("spot-text").textContent = state[0];
    $("spot-action").textContent = state[1];
  }

  function setSpot(state, anchor = null) {
    spotState = state;
    spot = anchor;
    showSpot();
    drawPins();
  }

  if (mode) {
    $("spot-action").addEventListener("click", () => {
      setSpot(spotState === "whole" ? "waiting" : "whole");
      text.focus();
    });
    // While a comment waits for its spot, a click on the image places the pin
    // instead of opening the image full size.
    $("image-link").addEventListener("click", (e) => {
      if (!pinModeOn()) return;
      e.preventDefault();
      const box = image.getBoundingClientRect();
      if (!box.width || !box.height) return;
      const fraction = (v) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000;
      setSpot("placed", {
        type: "point",
        x: fraction((e.clientX - box.left) / box.width),
        y: fraction((e.clientY - box.top) / box.height),
      });
      say("form-status", "");
      text.focus({ preventScroll: true });
    });
    image.addEventListener("load", fitPins);
    if (window.ResizeObserver) new ResizeObserver(fitPins).observe(image);
    window.addEventListener("resize", fitPins);
  }

  // --- Actions --------------------------------------------------------------

  // A token-holder's edit or delete that the server refused for good: the
  // agent has read the comment, or the token is not this comment's.
  async function lostToken(c, e) {
    storage.remove(tokenKey(c.n));
    editing = null;
    await load();
    const why = e.code === "already_read"
      ? `The agent has already read comment ${c.n}; it can no longer be changed.`
      : `Comment ${c.n} cannot be changed from this browser.`;
    say("comments-status", why, true);
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
    await busy(b, c.resolved ? "Reopening…" : "Resolving…", async () => {
      try {
        replace(await call("PATCH", `${base}/${c.n}`, { resolved: !c.resolved }));
        render();
        say("comments-status", `Comment ${c.n} ${c.resolved ? "reopened" : "resolved"}.`);
      } catch (e) {
        if (e.status === 404) await load();
        say("comments-status", e.status === 404 ? `Comment ${c.n} was deleted.` : e.message, true);
      }
    });
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

  async function remove(c, token, b) {
    if (!window.confirm(`Delete comment ${c.n}?`)) return;
    await busy(b, "Deleting…", async () => {
      try {
        await call("DELETE", `${base}/${c.n}`, undefined, { "X-Edit-Token": token });
      } catch (e) {
        if (e.status === 403 || e.status === 409) return lostToken(c, e);
        if (e.status !== 404) { say("comments-status", e.message, true); return; }
      }
      storage.remove(tokenKey(c.n));
      changed(comments.filter((x) => x.n !== c.n));
      render();
      say("comments-status", `Comment ${c.n} deleted.`);
      $("comments-title").focus();
    });
  }

  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (send.disabled) return;
    if (!text.value.trim()) {
      say("form-status", "Write a comment first.", true);
      text.focus();
      return;
    }
    if (mode && spotState === "waiting") {
      say("form-status", "Click a spot on the image, or choose “Whole file instead”.", true);
      return;
    }
    // Names are one line; the text keeps its line breaks.
    const name = author.value.replace(/[\r\n\t]+/g, " ").trim();
    author.value = name;
    storage.set(NAME_KEY, name);
    const body = { author: name, text: text.value, anchor: spotState === "placed" ? spot : null };
    await busy(send, "Sending…", async () => {
      try {
        const posted = await call("POST", base, body);
        const { edit_token: token, ...c } = posted;
        const kept = storage.set(tokenKey(c.n), token);
        changed([...comments, c]);
        text.value = "";
        if (mode) spotState = "waiting";
        spot = null;
        fold(true);
        light("comment", c.n);
        render();
        say("form-status", kept
          ? "Sent. You can edit or delete it until the agent reads it."
          : "Sent. This browser cannot store it, so it cannot be edited here.");
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

  // --- The panel ------------------------------------------------------------

  function openPanel(focus = true) {
    panel.hidden = false;
    toggle.setAttribute("aria-expanded", "true");
    showSpot();
    fitPins();
    load();
    if (focus) $("comments-title").focus();
  }

  function closePanel() {
    const inside = panel.contains(document.activeElement);
    panel.hidden = true;
    toggle.setAttribute("aria-expanded", "false");
    if (!text.value.trim() && spotState !== "placed") form.classList.add("folded");
    showSpot();
    fitPins();
    if (inside) toggle.focus();
  }

  toggle.addEventListener("click", () => (panel.hidden ? openPanel() : closePanel()));
  $("comments-close").addEventListener("click", closePanel);
  showResolved.addEventListener("click", () => {
    resolvedShown = !resolvedShown;
    render();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !panel.hidden) closePanel();
  });
  // Others may have commented while this tab was in the background.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") load();
  });

  if (/Mac|iPhone|iPad/.test(navigator.platform)) $("comment-hint").textContent = "Cmd+Enter to send";
  author.value = storage.get(NAME_KEY) || "";
  showSpot();
  load();
})();
