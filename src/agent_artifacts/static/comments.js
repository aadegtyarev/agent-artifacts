// Comments on the viewer page. The header's bubble turns comment mode on and
// off; in comment mode a tap on the content places a draft pin and opens the
// sheet with a field. Drafts stay on the server, seen only by this browser's
// author key, until Finish review publishes them all. On a phone the sheet
// has four heights (strip, a quarter, half, full); on a computer the same
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
  // The script in a page's frame: `ready` once it said so, `off` once it
  // clearly is not coming; `missing`: keys of pins it did not find.
  const page = { ready: false, off: false, missing: new Set(), hidden: new Set(), picking: false };
  const canPin = () => pinType === "point" || (pinType === "element" && page.ready);

  const desk = window.matchMedia("(min-width: 900px)");
  const UNDO_MS = 5000;

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
    // the sheet's height on a phone: strip, q (¼), h (½), f (full)
    pos: "strip",
    sel: null,
    // the new draft: {anchor, whole}; its text is in `fields.compose`
    compose: null,
    // the id of the draft being edited
    edit: null,
    comments: [],
    // drafts: the server's, with `gone` while a Delete waits for its Undo
    drafts: [],
  };
  // Every change bumps it, so an older GET cannot overwrite a newer state.
  let generation = 0;
  // Drafts deleted but not yet sent: id -> timer.
  const pending = new Map();

  // --- The server ----------------------------------------------------------------
  class ApiError extends Error {
    constructor(status, code, message) {
      super(message);
      this.status = status;
      this.code = code;
    }
  }

  async function call(method, path, payload, keepalive = false) {
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

  async function load() {
    const mine = ++generation;
    let found;
    try {
      found = await Promise.all([call("GET", "/comments"), call("GET", "/drafts")]);
    } catch (e) {
      if (mine === generation) toast(`Could not load comments. ${e.message}`, { error: true, ms: 8000 });
      return;
    }
    if (mine !== generation) return; // something newer has landed meanwhile
    const [comments, drafts] = found;
    S.comments = comments;
    // Pending deletes and edits go by id, so they hold across this.
    S.drafts = drafts;
    // An item that is no longer there leaves the view.
    if (S.sel && !items().some((i) => i.key === S.sel)) unselect();
    render();
  }

  const changed = () => { generation++; };

  // --- Items -----------------------------------------------------------------
  const LETTERS = "abcdefghijklmnopqrstuvwxyz";
  const letter = (i) => LETTERS[i % 26] + (i >= 26 ? String(Math.floor(i / 26) + 1) : "");
  const liveDrafts = () => S.drafts.filter((d) => !pending.has(d.id));
  const draftById = (id) => S.drafts.find((d) => d.id === id) || null;

  // The person's drafts, then the published comments. Drafts stay in sight
  // with comment mode off, so the page can be used (another screen of an
  // app, say) without them disappearing.
  function items() {
    const list = [];
    liveDrafts().forEach((d, i) => list.push({
      key: `d${d.id}`, draft: true, own: true, label: letter(i), anchor: d.anchor, text: d.text, d,
    }));
    for (const c of S.comments) {
      list.push({ key: `c${c.n}`, draft: false, own: c.mine === true, label: String(c.n),
        anchor: c.anchor, text: c.text, c });
    }
    return list;
  }
  const itemOf = (key) => items().find((i) => i.key === key) || null;

  // An item's pin is drawn when its anchor fits this content.
  const pinnable = (anchor) => Boolean(anchor && pinType && anchor.type === pinType);
  const lost = (it) => pinType === "element" && pinnable(it.anchor)
    && ((page.ready && page.missing.has(it.key)) || page.off);
  // Its element is on the page but not shown: another screen of an app, a
  // closed tab. Not an error, just not here now.
  const away = (it) => lost(it) && !page.off && page.hidden.has(it.key);
  const lostText = (it) => (away(it) ? "on another screen" : "spot not found");
  const lostClass = (it) => (lost(it) && !page.off && !away(it) ? " lost" : "");

  // Where an item is, in a few words.
  function where(it) {
    if (!it.anchor) return "about the whole file";
    if (it.anchor.type === "point") return "on the image";
    return it.anchor.text ? `“${short(it.anchor.text, 24)}”` : "on the page";
  }
  const short = (s, n) => ([...s].length > n ? `${[...s].slice(0, n - 1).join("").trimEnd()}…` : s);

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

  function miniPin(it) {
    const pin = el("span", `pin mini${it.own ? " own" : ""}${it.draft ? " draft" : ""}`,
      it.anchor ? it.label : "≡");
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
  };
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

  // --- Drawing -------------------------------------------------------------------
  function render() {
    // A field with the focus is put back with its caret after the redraw.
    const active = document.activeElement;
    const kept = Object.values(fields).includes(active) ? active : null;
    const caret = kept ? [kept.selectionStart, kept.selectionEnd] : null;

    const shown = S.mode || S.comments.length > 0 || liveDrafts().length > 0;
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
    const [content, footer] = open ? views[S.view]() : [[], []];
    body.replaceChildren(...content);
    foot.replaceChildren(...footer);
    for (const area of body.querySelectorAll("textarea")) grow(area);
    for (const area of foot.querySelectorAll("textarea")) grow(area);
    if (kept && kept.isConnected) {
      kept.focus({ preventScroll: true });
      kept.setSelectionRange(...caret);
    }
    drawPins();
    syncPicking();
    dockToast();
    // The floating menu button of full screen (viewer.js) keeps off the sheet.
    document.dispatchEvent(new CustomEvent("aa:sheet"));
  }

  function renderStrip() {
    strip.replaceChildren(stripText);
    const drafts = liveDrafts().length;
    let line;
    if (drafts) line = `Review · ${drafts}`;
    else if (S.comments.length) line = `Comments · ${S.comments.length}${desk.matches ? "" : " ›"}`;
    else if (S.mode && canPin()) line = `Tap a spot on the ${pinType === "point" ? "image" : "page"} to comment`;
    else line = "No comments yet";
    stripText.textContent = line;
    // In comment mode, with comments to list, the hint goes to the right.
    if (!drafts && S.comments.length && S.mode && canPin()) {
      strip.append(el("span", "strip-hint", "tap a spot to add"));
    }
    if (drafts && S.view !== "finish") {
      strip.append(button("Finish review", (e) => { e.stopPropagation(); openFinish(); }, "btn pri"));
    }
  }

  const views = {
    list() {
      const all = items();
      const drafts = all.filter((i) => i.draft).length;
      const out = [];
      let title = "No comments yet";
      if (drafts) title = `Review · ${drafts} ${drafts === 1 ? "draft" : "drafts"}`
        + (S.comments.length ? ` · ${S.comments.length} published` : "");
      else if (S.comments.length) title = `Comments · ${S.comments.length}`;
      out.push(el("h2", "ttl handle", title));
      if (!all.length) {
        out.push(el("p", "empty", S.mode && canPin()
          ? `Tap a spot on the ${pinType === "point" ? "image" : "page"} to leave a comment.`
          : S.mode ? "Nobody has commented yet." : "Turn on the speech bubble to comment."));
      }
      for (const it of all) out.push(row(it, status(it)));
      if (S.mode) {
        const add = el("div", "addwhole");
        add.append(button("+ About the whole file", () => composeWhole()));
        out.push(add);
      }
      if (!stored && S.mode) out.push(el("p", "note", "This browser keeps nothing: your drafts last only while this page is open."));
      // On a computer Finish review is in the strip's line heading the panel.
      return [out, drafts && !desk.matches ? [finishBar(drafts)] : []];
    },

    item() {
      const it = itemOf(S.sel);
      if (!it) return views.list();
      const all = items();
      const at = all.findIndex((i) => i.key === it.key);
      const nav = el("div", "nav handle");
      nav.append(
        button("‹ All", () => go("list"), "btn all"),
        button(`${at + 1} of ${all.length} · ${where(it)}`, () => go("list", "h"), "lbl"),
      );
      const prev = button("", () => step(-1), "chev");
      prev.append(icon("left"));
      prev.setAttribute("aria-label", "Previous");
      const next = button("", () => step(1), "chev");
      next.append(icon("right"));
      next.setAttribute("aria-label", "Next");
      nav.append(prev, next);

      const msg = el("div", `msg${it.draft ? " unsent" : ""}`);
      const bd = el("div", "bd");
      const who = el("span", "who", it.draft ? "Your draft" : it.c.author);
      const time = el("span", "when", when(it.draft ? it.d.updated_at : it.c.created_at));
      who.append(time);
      bd.append(who);
      if (it.draft && S.edit === it.d.id) {
        const acts = el("div", "acts");
        acts.append(button("Save", () => saveEdit(it.d), "btn pri"), button("Cancel", () => {
          S.edit = null;
          render();
        }));
        bd.append(fields.edit, acts);
      } else {
        bd.append(el("div", "text", it.text));
        const notes = [];
        if (!it.draft && it.c.before_replace) notes.push("written before the file was replaced");
        if (lost(it)) notes.push(page.off ? `on ${where(it)}` : `${lostText(it)} · ${where(it)}`);
        if (notes.length) bd.append(el("span", `st${lostClass(it)}`, notes.join(" · ")));
        if (it.draft) {
          const acts = el("div", "acts");
          acts.append(button("Edit", () => startEdit(it.d)), button("Delete", () => deleteDraft(it.d)));
          bd.append(acts);
        }
      }
      msg.append(miniPin(it), bd);
      const out = [nav, msg];
      // Pulled up to half or full on a phone: the others under it.
      if (!desk.matches && (S.pos === "h" || S.pos === "f")) {
        const others = all.filter((o) => o.key !== it.key);
        if (others.length) out.push(el("p", "sec", "Others"));
        for (const o of others) out.push(row(o, status(o)));
      }
      return [out, []];
    },

    compose() {
      const c = S.compose;
      const out = [el("p", "cap handle", c.anchor ? "New draft" : "New draft · about the whole file")];
      if (!stored) out.push(el("p", "note", "This browser keeps nothing: your drafts last only while this page is open."));
      const pill = el("div", "pill");
      pill.append(fields.compose, sendButton);
      sendButton.disabled = !fields.compose.value.trim();
      // On a phone the field is the sheet's footer, so it stays at the
      // bottom edge, right above the keyboard.
      return desk.matches ? [[...out, pill], []] : [out, [pill]];
    },

    finish() {
      const drafts = items().filter((i) => i.draft);
      const out = [el("h2", "ttl handle", `Finish review · ${drafts.length}`)];
      if (!drafts.length) out.push(el("p", "empty", "No drafts: you can publish a general comment only."));
      for (const it of drafts) out.push(row(it, where(it)));
      const general = el("div", "general");
      general.append(fields.general);
      out.push(general, el("p", "note", "Everyone will see them. Ask the agent to look at the comments."));
      const bar = el("div", "foot2");
      const done = button("Finish review", () => finish(done), "btn pri big");
      finishButton = done;
      done.disabled = !drafts.length && !fields.general.value.trim();
      bar.append(button("Back", () => go("list", "h")), done);
      return [out, [bar]];
    },
  };

  // Finish review on the finish view, enabled by a general comment.
  let finishButton = null;

  function status(it) {
    if (it.draft) return lost(it) && !page.off ? `your draft · ${lostText(it)}` : "your draft";
    const parts = [it.c.author];
    if (it.own) parts.push("you");
    parts.push(when(it.c.created_at));
    if (lost(it) && !page.off) parts.push(lostText(it));
    return parts.join(" · ");
  }

  function row(it, line) {
    const r = el("button", `it${it.draft && S.view !== "finish" ? " dr" : ""}${S.sel === it.key && S.view === "list" ? " cur" : ""}`);
    r.type = "button";
    r.dataset.key = it.key;
    const tx = el("span", "tx");
    tx.append(el("span", "main", (it.anchor ? "" : "About the whole file: ") + it.text));
    const st = el("span", `st${lostClass(it)}`, line);
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
  function pinList() {
    const shown = items().filter((it) => pinnable(it.anchor)).map((it) => ({
      key: it.key, label: it.label, anchor: it.anchor, own: it.own, draft: it.draft,
      lit: S.sel === it.key && (S.view === "item"
        || (S.view === "list" && (desk.matches || S.pos !== "strip"))),
    }));
    if (S.compose && pinnable(S.compose.anchor)) {
      shown.push({ key: "new", label: "", anchor: S.compose.anchor, own: true, draft: true, lit: true });
    }
    return shown;
  }

  // The frame gets a draft's place in the list ("d0", "d1"…) as its key,
  // never its id: the page's own scripts can read what the frame gets.
  let frameKeys = new Map();
  const toFrame = (key) => {
    const found = [...frameKeys].find(([, k]) => k === key);
    if (found) return found[0];
    return key.startsWith("c") || key === "new" ? key : null;
  };
  const fromFrame = (key) => frameKeys.get(key) || key;

  function drawPins() {
    if (pinType === "element") {
      if (!page.ready) return;
      frameKeys = new Map();
      let n = 0;
      const pins = pinList().map((pin) => {
        if (!pin.draft || pin.key === "new") return pin;
        const key = `d${n++}`;
        frameKeys.set(key, pin.key);
        return { ...pin, key };
      });
      tell({ type: "pins", pins });
      return;
    }
    if (pinType !== "point") return;
    const drawn = pinList().map((p) => {
      const pin = el("button", `pin${p.own ? " own" : ""}${p.draft ? " draft" : ""}${p.lit ? " lit" : ""}`, p.label);
      pin.type = "button";
      pin.dataset.key = p.key;
      // Percentages of the layer, which is the size of the image.
      pin.style.left = `${p.anchor.x * 100}%`;
      pin.style.top = `${p.anchor.y * 100}%`;
      pin.setAttribute("aria-label", p.key === "new" ? "The spot of the new draft"
        : `${p.draft ? "Draft" : "Comment"} ${p.label}`);
      return pin;
    });
    pinLayer.replaceChildren(...drawn);
    pinLayer.hidden = drawn.length === 0;
    fitPins();
  }

  // The layer covers exactly the displayed image. A pin too close to the
  // right or top edge turns to the other side to stay on the image.
  function fitPins() {
    if (pinType !== "point") return;
    const width = image.offsetWidth;
    const height = image.offsetHeight;
    pinLayer.style.left = `${image.offsetLeft}px`;
    pinLayer.style.top = `${image.offsetTop}px`;
    pinLayer.style.width = `${width}px`;
    pinLayer.style.height = `${height}px`;
    for (const pin of pinLayer.children) {
      const x = parseFloat(pin.style.left) / 100;
      const y = parseFloat(pin.style.top) / 100;
      // By the selected pin's size, so a pin keeps its side when selected.
      pin.classList.toggle("left", x * width + LIT > width && x * width >= LIT);
      pin.classList.toggle("down", y * height < LIT);
    }
  }

  const LIT = 32;

  // Brings a pin into the middle of what is visible of the content.
  function reveal(key) {
    if (pinType === "element") {
      const shown = toFrame(key);
      if (page.ready && shown) tell({ type: "show", key: shown });
      return;
    }
    if (pinType !== "point") return;
    const pin = pinLayer.querySelector(`[data-key="${CSS.escape(key)}"]`);
    if (!pin) return;
    const box = imageBox.getBoundingClientRect();
    const r = pin.getBoundingClientRect();
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    imageBox.scrollBy({
      top: r.top + r.height / 2 - (box.top + box.bottom) / 2,
      left: r.left + r.width / 2 - (box.left + box.right) / 2,
      behavior: smooth ? "smooth" : "auto",
    });
  }
  // After the sheet has its new height.
  const revealSoon = (key) => requestAnimationFrame(() => requestAnimationFrame(() => reveal(key)));

  // A tap on a pin: with the list open, its row lights up and comes into
  // view, the sheet stays; otherwise it opens its item. On the new draft's
  // pin, its field.
  function tapPin(key) {
    if (key === "new") {
      if (S.compose) {
        if (S.view !== "compose" || S.pos === "strip") go("compose", "q");
        fields.compose.focus({ preventScroll: true });
      }
      return;
    }
    if (!itemOf(key)) return;
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

  // On a phone a pin's tap area is larger than the pin, so the areas of pins
  // close together overlap: a tap goes to the pin nearest to it.
  function nearestPin(x, y) {
    let best = null;
    let distance = Infinity;
    for (const pin of pinLayer.children) {
      const r = pin.getBoundingClientRect();
      const d = (r.left + r.width / 2 - x) ** 2 + (r.top + r.height / 2 - y) ** 2;
      if (d < distance) { distance = d; best = pin; }
    }
    return best;
  }

  if (pinType === "point") {
    pinLayer.addEventListener("click", (e) => {
      const hit = e.target.closest(".pin");
      if (!hit) return;
      e.preventDefault();
      e.stopPropagation();
      // A keyboard press has no point (detail 0): it is the focused pin.
      const pin = e.detail ? nearestPin(e.clientX, e.clientY) || hit : hit;
      tapPin(pin.dataset.key);
    });
    // In comment mode a tap places a draft pin; otherwise it does nothing.
    // A Ctrl/Cmd or middle click still opens the image full size.
    $("image-link").addEventListener("click", (e) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
      e.preventDefault();
      if (!S.mode) return;
      const box = image.getBoundingClientRect();
      if (!box.width || !box.height) return;
      const fraction = (v) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000;
      place({
        type: "point",
        x: fraction((e.clientX - box.left) / box.width),
        y: fraction((e.clientY - box.top) / box.height),
      });
    });
    image.addEventListener("load", fitPins);
    // As the sheet grows or shrinks, the image moves in its box.
    if (window.ResizeObserver) {
      const watch = new ResizeObserver(fitPins);
      watch.observe(image);
      watch.observe(imageBox);
    }
  }

  // --- Pins on a page: the script in the frame -------------------------------------
  const tell = (m) => frame.contentWindow.postMessage(m, "*");

  // In comment mode the script takes a tap on the page as a spot.
  function syncPicking() {
    if (pinType !== "element" || !page.ready) return;
    const on = S.mode && dialog.hidden;
    if (on !== page.picking) tell({ type: "mode", on });
    page.picking = on;
  }

  // Messages from the frame: only from its window, checked like any input.
  const isAnchor = (a) => a && typeof a === "object" && a.type === "element"
    && typeof a.selector === "string" && [...a.selector].length <= 512
    && typeof a.text === "string" && [...a.text].length <= 200
    && [a.x, a.y].every((v) => typeof v === "number" && v >= 0 && v <= 1)
    && Number.isInteger(a.width) && a.width > 0 && a.width <= 100000;
  const isKey = (k) => typeof k === "string" && /^[A-Za-z0-9_-]{1,24}$/.test(k);

  function onFrameMessage(e) {
    if (e.source !== frame.contentWindow) return;
    const m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.type === "ready") {
      clearTimeout(readyTimer);
      page.ready = true;
      page.off = false;
      // A page loaded again starts with pin mode off.
      page.picking = false;
      render();
    } else if (!page.ready) {
      // Nothing else counts before the script has said it is there.
    } else if (m.type === "anchor" && page.picking && isAnchor(m.anchor)) {
      const { selector, text, x, y, width } = m.anchor;
      place({ type: "element", selector, text, x, y, width });
    } else if (m.type === "placed" && Array.isArray(m.missing) && m.missing.length <= 500
      && m.missing.every(isKey)) {
      const missing = new Set(m.missing.map(fromFrame));
      const hidden = new Set((Array.isArray(m.hidden) && m.hidden.length <= 500 ? m.hidden : [])
        .filter(isKey).map(fromFrame));
      const same = missing.size === page.missing.size && [...missing].every((k) => page.missing.has(k))
        && hidden.size === page.hidden.size && [...hidden].every((k) => page.hidden.has(k));
      page.missing = missing;
      page.hidden = hidden;
      // The list and the item say which spots were not found; a field being
      // typed in is not redrawn for that.
      if (!same && !Object.values(fields).includes(document.activeElement)) render();
    } else if (m.type === "pin" && isKey(m.key)) {
      tapPin(fromFrame(m.key));
    } else if (m.type === "escape" && page.picking) {
      escape();
    }
  }

  // The script may have reported ready before this page listened; asked
  // here and when the frame has loaded, it answers. With no answer soon
  // after, the page blocks it: comments without pins.
  let readyTimer = null;
  function waitForScript(ms) {
    clearTimeout(readyTimer);
    if (page.ready) return;
    readyTimer = setTimeout(() => {
      page.off = true;
      render();
    }, ms);
  }

  if (pinType === "element") {
    window.addEventListener("message", onFrameMessage);
    frame.addEventListener("load", () => {
      // Another document may be in the frame now (a link, a refresh): it
      // has pins only once its script says so.
      page.ready = false;
      page.picking = false;
      page.missing = new Set();
      page.hidden = new Set();
      render();
      tell({ type: "hello" });
      waitForScript(2000);
    });
    tell({ type: "hello" });
    waitForScript(4000);
  }

  // --- Navigation ------------------------------------------------------------------
  // ¼ is "as tall as its content, at most half".
  const DEFAULT_POS = { list: "h", item: "q", compose: "q", finish: "h" };

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

  function openItem(key) {
    // An edit with typed changes is not dropped by moving to another item.
    if (S.edit && S.sel !== key) {
      const it = itemOf(S.sel);
      if (it && it.draft && fields.edit.value !== it.d.text) {
        toast("Save or cancel the edit first");
        return;
      }
    }
    if (S.compose) dropCompose(false);
    if (S.sel !== key) S.edit = null;
    S.sel = key;
    // One comment: the sheet fits it. Pulled up with the others under it,
    // it keeps its height and the chosen one comes to the top.
    const keep = S.view === "item" && (S.pos === "h" || S.pos === "f");
    go("item", keep ? S.pos : "q");
    if (keep) body.scrollTop = 0;
    revealSoon(key);
  }

  function step(by) {
    const all = items();
    if (!all.length) return;
    const at = all.findIndex((i) => i.key === S.sel);
    openItem(all[(at + by + all.length) % all.length].key);
  }

  // Back to the strip on a phone, to the list on a computer.
  function fold() {
    S.sel = null;
    S.edit = null;
    S.view = "list";
    S.pos = "strip";
    render();
  }

  // --- Comment mode and the name ---------------------------------------------------
  let afterName = null;

  function askName(then) {
    afterName = then;
    nameInput.value = "";
    dialog.hidden = false;
    syncPicking();
    nameInput.focus();
  }

  function closeName(value) {
    dialog.hidden = true;
    const then = afterName;
    afterName = null;
    if (value === null) {
      toggle.focus({ preventScroll: true });
      syncPicking();
      return;
    }
    name = cleanName(value);
    storage.set(NAME_KEY, name);
    if (then) then();
  }

  $("name-form").addEventListener("submit", (e) => {
    e.preventDefault();
    closeName(nameInput.value);
  });
  $("name-skip").addEventListener("click", () => closeName(""));
  dialog.addEventListener("click", (e) => { if (e.target === dialog) closeName(null); });

  function setMode(on, then = null) {
    if (on && name === null) {
      askName(() => setMode(true, then));
      return;
    }
    S.mode = on;
    if (!on) {
      if (S.compose) dropCompose(false);
      const it = itemOf(S.sel);
      if (!it || S.view === "finish") {
        S.sel = null;
        S.edit = null;
        S.view = "list";
        if (S.pos !== "h") S.pos = "strip";
      }
    } else if (!canPin() && !desk.matches && S.pos === "strip") {
      // Nothing to tap on: the list, with "About the whole file".
      S.view = "list";
      S.pos = "h";
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
      if (S.view !== "compose" || S.pos === "strip") go("compose", "q");
      else drawPins();
    } else {
      if (S.compose) dropCompose(true);
      S.compose = { anchor };
      S.sel = null;
      go("compose", "q");
    }
    fields.compose.focus({ preventScroll: true });
    revealSoon("new");
  }

  function composeWhole() {
    if (S.compose) dropCompose(true);
    S.compose = { anchor: null };
    S.sel = null;
    go("compose", "q");
    fields.compose.focus({ preventScroll: true });
  }

  // Drops the new draft; typed text comes back with Undo unless `quiet`.
  function dropCompose(quiet) {
    const kept = { compose: S.compose, text: fields.compose.value };
    S.compose = null;
    fields.compose.value = "";
    if (quiet || !kept.text.trim()) return;
    toast("Draft discarded", {
      undo: () => {
        if (!S.mode) return;
        S.compose = kept.compose;
        fields.compose.value = kept.text;
        go("compose", "q");
        fields.compose.focus({ preventScroll: true });
      },
    });
  }

  // The server refused the name (one kept from an older version, say): it
  // is forgotten and asked again; the text stays where it is.
  function nameRefused(e) {
    if (e.status !== 400 || !/^author\b/i.test(e.message)) return false;
    name = null;
    try { localStorage.removeItem(NAME_KEY); } catch { /* not kept anyway */ }
    toast("That name cannot be used. Give another one.", { error: true, ms: 6000 });
    askName(null);
    return true;
  }

  async function addDraft() {
    const c = S.compose;
    const text = fields.compose.value;
    if (!c || !text.trim() || sendButton.disabled) return false;
    sendButton.disabled = true;
    try {
      const draft = await call("POST", "/drafts", { author: name || "", text, anchor: c.anchor });
      changed();
      S.drafts.push(draft);
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
      if (!nameRefused(e)) toast(e.message, { error: true, ms: 8000 });
      return false;
    } finally {
      sendButton.disabled = !fields.compose.value.trim();
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
      toast(e.message, { error: true, ms: 8000 });
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
        toast(`Could not delete the draft. ${e.message}`, { error: true, ms: 8000 });
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
      go("finish", "h");
    };
    if (S.mode) show();
    else setMode(true, show);
  }

  async function finish(done) {
    done.disabled = true;
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
      const made = await call("POST", "/review", payload);
      changed();
      S.comments = [...S.comments, ...made];
      S.drafts = [];
      fields.general.value = "";
      S.mode = false;
      S.compose = null;
      S.sel = null;
      S.view = "list";
      S.pos = "strip";
      render();
      toast(`Published ${made.length} ${made.length === 1 ? "comment" : "comments"}`);
    } catch (e) {
      if (!nameRefused(e)) toast(e.message, { error: true, ms: 8000 });
      // The drafts on the server may differ now (one gone, say).
      load();
    } finally {
      if (done.isConnected) done.disabled = !liveDrafts().length && !fields.general.value.trim();
    }
  }

  // --- Fields ------------------------------------------------------------------
  fields.compose.addEventListener("input", () => {
    grow(fields.compose);
    sendButton.disabled = !fields.compose.value.trim();
  });
  fields.edit.addEventListener("input", () => grow(fields.edit));
  fields.general.addEventListener("input", () => {
    grow(fields.general);
    const done = finishButton;
    if (done && done.isConnected) done.disabled = !liveDrafts().length && !fields.general.value.trim();
  });
  // Enter adds the draft; Shift+Enter is a new line.
  fields.compose.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      addDraft();
    }
  });
  fields.edit.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      const it = itemOf(S.sel);
      if (it && it.draft) saveEdit(it.d);
    }
  });

  // --- The sheet: tap and drag -------------------------------------------------------
  // A tap on the strip opens the list. Dragging the grip, the title line (its
  // buttons too) or the strip moves the sheet with the finger; let go, it
  // snaps to the height nearest to where it is and keeps what it shows.
  const POS = ["strip", "q", "h", "f"];
  let drag = null;

  function openList() {
    go("list", "h");
  }

  strip.addEventListener("click", (e) => {
    if (e.target.closest("button") || dragged()) return;
    if (desk.matches) go("list");
    else openList();
  });

  const onHandle = (t) => t === grip || !!t.closest(".sbody > .handle");

  function startDrag(e) {
    if (desk.matches || e.button > 0 || drag) return;
    if (e.target.closest("button") && !onHandle(e.target)) return;
    // From a button it takes more than a finger's tap slop to be a drag.
    const slop = e.target.closest("button") ? 16 : 6;
    drag = { id: e.pointerId, y: e.clientY, from: sheet.getBoundingClientRect().height,
      pos: S.pos, slop, moved: false };
  }
  sheet.addEventListener("pointerdown", (e) => {
    // Only the click that ends a drag or a height step is swallowed.
    draggedAt = -Infinity;
    // A drag whose pointerup was lost (its node redrawn away) is dropped.
    if (drag && !drag.moved) drag = null;
    if (onHandle(e.target) || strip.contains(e.target)) startDrag(e);
  });
  sheet.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    const dy = e.clientY - drag.y;
    if (!drag.moved && Math.abs(dy) < drag.slop) return;
    if (!drag.moved) {
      drag.moved = true;
      // Captured only once it is a drag, so a tap stays a click on the strip.
      sheet.setPointerCapture(e.pointerId);
      sheet.classList.add("dragging");
      // From the strip the sheet opens on the list as it is pulled.
      if (S.pos === "strip") {
        S.pos = "h";
        S.view = "list";
        render();
      }
    }
    const max = sheet.parentElement.clientHeight;
    sheet.style.height = `${Math.max(44, Math.min(max, drag.from - dy))}px`;
  });
  let draggedAt = -Infinity;
  const dragged = () => performance.now() - draggedAt < 400;

  function endDrag(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const d = drag;
    drag = null;
    const now = sheet.getBoundingClientRect().height;
    sheet.classList.remove("dragging");
    sheet.style.height = "";
    // A tap is handled by its click, which then lands on what was tapped.
    if (!d.moved) return;
    draggedAt = performance.now();
    const stage = sheet.parentElement.clientHeight;
    const heights = { strip: 44, q: stage * 0.3, h: stage * 0.52, f: stage };
    // The height it started from is where it was, not the nominal one, so a
    // small slip snaps back instead of folding a short ¼ sheet.
    if (d.pos in heights && d.pos !== "strip") heights[d.pos] = d.from;
    const up = now > d.from;
    let best = "strip";
    let distance = Infinity;
    for (const p of POS) {
      const dd = Math.abs(heights[p] - now);
      if (dd < distance) { distance = dd; best = p; }
    }
    // Let go well below a quarter: folded.
    if (!up && now < Math.min(heights.q, stage * 0.3) * 0.75) best = "strip";
    // A drag changes the height only: what the sheet shows stays.
    if (best === "strip") {
      if (S.compose) dropCompose(false);
      fold();
    } else if (best === "q" && S.view !== "item" && S.view !== "compose") {
      go(S.view, "h");
    } else go(S.view, best);
  }
  sheet.addEventListener("pointerup", endDrag);
  // A drag that began on a button of the title line is not a tap on it.
  sheet.addEventListener("click", (e) => {
    if (dragged()) { e.stopPropagation(); e.preventDefault(); }
  }, true);
  // A tap on the grip steps the height and keeps the view. A tap on the
  // title line does the same on the list; on one comment it goes back to the
  // list; on a new draft's caption or the Finish title it does nothing.
  sheet.addEventListener("click", (e) => {
    if (desk.matches || !onHandle(e.target) || e.target.closest("button")) return;
    if (e.target !== grip) {
      if (S.view === "item") { go("list", "h"); return; }
      if (S.view !== "list") return;
    }
    go(S.view, S.pos === "h" ? "f" : "h");
  });
  sheet.addEventListener("pointercancel", endDrag);

  // --- Keys, resizes, coming back ---------------------------------------------------
  function escape() {
    if (!dialog.hidden) closeName(null);
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
      else fold();
    }
  }
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !e.defaultPrevented) escape();
  });

  // The keyboard or a turned phone resizes the page: the pin being written
  // about stays in sight above the sheet.
  let resizeFrame = 0;
  window.addEventListener("resize", () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      fitPins();
      dockToast();
      if (S.compose && S.compose.anchor) reveal("new");
      else if (S.view === "item" && S.sel) reveal(S.sel);
    });
  });
  desk.addEventListener("change", () => {
    if (!desk.matches && S.pos === "strip" && S.view !== "list") S.pos = DEFAULT_POS[S.view];
    render();
  });
  // Others may have commented while this tab was in the background.
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") load();
  });

  render();
  load();
})();
