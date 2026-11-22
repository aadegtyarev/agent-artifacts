// Comment pins on the content: drawn over an image here, or in a page's
// frame by pins.js, which this talks to by messages. Loaded as its own
// script before comments.js, which calls this once with its elements, its
// state `S` (read on every call) and what a pin or a spot does to the view:
//   items, itemOf, pinnable: what is listed, and which of it has a pin here
//   go, openItem, dropCompose, render: move between the sheet's views
//   place(anchor): a spot was chosen for a new draft
//   escape(): Escape was pressed in the page
// It returns `page`, what is known of the frame's script, and the calls the
// sheet makes when it redraws or moves.
const commentsPins = ({
  pinType, image, imageBox, imageLink, pinLayer, frame, body, dialog,
  S, desk, fields, items, itemOf, pinnable, el,
  go, openItem, dropCompose, render, place, escape,
}) => {
  const { badge: badgeEl } = pinMarks;
  // The script in a page's frame: `ready` once it said so, `off` once it
  // clearly is not coming; `missing`: keys of pins it did not find.
  const page = { ready: false, off: false, missing: new Set(), hidden: new Set(), picking: false };

  function pinList() {
    const shown = items().filter((it) => pinnable(it.anchor)).map((it) => ({
      key: it.key, label: it.label, anchor: it.anchor, own: it.own, fill: it.fill, badge: it.badge,
      lit: S.sel === it.key && (S.view === "item"
        || (S.view === "list" && (desk.matches || S.pos !== "strip"))),
    }));
    if (S.compose && pinnable(S.compose.anchor)) {
      shown.push({ key: "new", label: "", anchor: S.compose.anchor, own: true, fill: "draft", badge: "", lit: true });
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
        if (!pin.key.startsWith("d")) return pin;
        const key = `d${n++}`;
        frameKeys.set(key, pin.key);
        return { ...pin, key };
      });
      tell({ type: "pins", pins });
      return;
    }
    if (pinType !== "point") return;
    const drawn = pinList().map((p) => {
      const pin = el("button", `pin${p.own ? " own" : ""}${p.fill !== "solid" ? ` ${p.fill}` : ""}${p.lit ? " lit" : ""}`, p.label);
      if (p.badge) pin.append(badgeEl(p.badge));
      pin.type = "button";
      pin.dataset.key = p.key;
      // Percentages of the layer, which is the size of the image.
      pin.style.left = `${p.anchor.x * 100}%`;
      pin.style.top = `${p.anchor.y * 100}%`;
      pin.setAttribute("aria-label", p.key === "new" ? "The spot of the new draft"
        : `${p.fill === "draft" ? "Draft" : "Comment"} ${p.label}`);
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
    imageLink.addEventListener("click", (e) => {
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
    && Number.isInteger(a.width) && a.width > 0 && a.width <= 100000
    && (a.screen === undefined || (typeof a.screen === "string" && [...a.screen].length <= 80))
    && (a.hash === undefined || (typeof a.hash === "string" && [...a.hash].length <= 200
      && /^#[\x21-\x7e]*$/.test(a.hash)));
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
      const { selector, text, x, y, width, screen, hash } = m.anchor;
      const anchor = { type: "element", selector, text, x, y, width };
      // Single-line, as the server wants: names come from the page.
      const one = (s) => s.replace(/[\t\n\r]+/g, " ").trim();
      if (screen && one(screen)) anchor.screen = one(screen);
      if (hash && !/[\t\n\r]/.test(hash)) anchor.hash = hash;
      place(anchor);
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

  return { page, drawPins, fitPins, reveal, revealSoon, showRow, syncPicking };
};
