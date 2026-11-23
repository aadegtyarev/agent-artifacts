// Comment pins on the content: drawn over an image here, or in a page's
// frame by pins.js, which this talks to by messages. Loaded as its own
// script before comments.js, which calls this once with its elements, what
// to read from the sheet and what to tell it. The sheet decides what a pin
// or a spot does; this only draws and listens:
//   pins(): the pins to draw now ({key, label, anchor, tone, fill, badge, lit})
//   picking(): whether a tap on the page is taken as a spot
//   onPin(key): a pin was tapped
//   onSpot(anchor): a spot was chosen for a new draft (in comment mode)
//   onEscape(): Escape was pressed in the page while picking
//   onBlank(): outside comment mode, a plain tap on the content away from
//     pins and from anything the page acts on
//   onChange(spotsOnly): what is known of the page changed; with
//     `spotsOnly`, only which spots were not found or are hidden
// It returns `page`, what is known of the frame's script, and the calls the
// sheet makes when it redraws or moves.
const commentsPins = ({
  pinType, image, imageBox, imageLink, pinLayer, frame,
  pins, picking, onPin, onSpot, onEscape, onBlank, onChange,
}) => {
  const { badge: badgeEl, inside, STAR } = pinMarks;
  const TONES = { own: " own", other: "", agent: " ag" };
  // The script in a page's frame: `ready` once it said so, `off` once it
  // clearly is not coming; `missing`: keys of pins it did not find.
  const page = { ready: false, off: false, missing: new Set(), hidden: new Set(), picking: false };

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
      const shown = pins().map((pin) => {
        if (!pin.key.startsWith("d")) return pin;
        const key = `d${n++}`;
        frameKeys.set(key, pin.key);
        return { ...pin, key };
      });
      // How they look, from Settings (menu-dialogs.js): large, whose told
      // by shape too, and the dot's colour.
      const look = document.documentElement.classList;
      tell({ type: "pins", pins: shown, big: look.contains("pins-big"), shaped: look.contains("pins-shaped"),
        dot: document.documentElement.dataset.dot || "red" });
      return;
    }
    if (pinType !== "point") return;
    const drawn = pins().map((p) => {
      const pin = document.createElement("button");
      pin.className = `pin${TONES[p.tone] || ""}${p.fill !== "solid" ? ` ${p.fill}` : ""}${p.lit ? " lit" : ""}`;
      pin.append(inside(p.label));
      if (p.badge) pin.append(badgeEl(p.badge));
      pin.type = "button";
      pin.dataset.key = p.key;
      // Percentages of the layer, which is the size of the image.
      pin.style.left = `${p.anchor.x * 100}%`;
      pin.style.top = `${p.anchor.y * 100}%`;
      pin.setAttribute("aria-label", p.key === "new" ? "The spot of the new draft"
        : p.label === STAR ? "The agent's comment" : `Comment by ${p.label}`);
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
      const lit = document.documentElement.classList.contains("pins-big") ? LIT_BIG : LIT;
      pin.classList.toggle("left", x * width + lit > width && x * width >= lit);
      pin.classList.toggle("down", y * height < lit);
    }
  }

  const LIT = 36;
  const LIT_BIG = 45; // large pins, Settings' Pins: Large

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
      onPin(pin.dataset.key);
    });
    // In comment mode a tap places a draft pin (the sheet checks the mode);
    // otherwise it does nothing. A Ctrl/Cmd or middle click still opens the
    // image full size.
    imageLink.addEventListener("click", (e) => {
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey) return;
      e.preventDefault();
      const box = image.getBoundingClientRect();
      if (!box.width || !box.height) return;
      const fraction = (v) => Math.round(Math.min(1, Math.max(0, v)) * 10000) / 10000;
      onSpot({
        type: "point",
        x: fraction((e.clientX - box.left) / box.width),
        y: fraction((e.clientY - box.top) / box.height),
      });
    });
    // Outside comment mode a plain tap on the image or the space around it,
    // away from the pins, is a tap on an empty spot (the sheet checks the
    // mode): not a drag, a long press, a double click or one with a
    // modifier key. A press is a tap when it lifts within 500 ms and 10 px
    // of where it began.
    let press = null;
    imageBox.addEventListener("pointerdown", (e) => {
      press = e.isPrimary ? { x: e.clientX, y: e.clientY, at: performance.now(), tap: false } : null;
    }, { passive: true });
    imageBox.addEventListener("pointerup", (e) => {
      if (press && e.isPrimary) {
        press.tap = Math.hypot(e.clientX - press.x, e.clientY - press.y) <= 10 && performance.now() - press.at <= 500;
      }
    }, { passive: true });
    imageBox.addEventListener("pointercancel", () => { press = null; });
    imageBox.addEventListener("click", (e) => {
      const tapped = press && press.tap;
      press = null;
      if (!tapped || e.button !== 0 || e.detail !== 1 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      if (e.target instanceof Element && e.target.closest(".pin")) return;
      onBlank();
    });
    // Zoom of the image alone, see zoom.js: the image is drawn larger and
    // its box scrolls; the pins' layer takes the image's size (fitPins).
    // Its size at 1x is measured whenever it can change (the image loaded,
    // the window resized or turned), never before it has one.
    let zoom = 1;
    let base = null;
    const SIZES = ["width", "height", "maxWidth", "maxHeight", "flex"];
    const loaded = () => image.complete && image.naturalWidth > 0;
    function drawAt(s) {
      if (s !== 1 && !base) {
        for (const p of SIZES) image.style[p] = "";
        if (loaded() && image.offsetWidth && image.offsetHeight) {
          base = { w: image.offsetWidth, h: image.offsetHeight };
        }
      }
      if (s === 1 || !base) {
        base = null;
        for (const p of SIZES) image.style[p] = "";
        zoom = 1;
      } else {
        Object.assign(image.style, { width: `${base.w * s}px`, height: `${base.h * s}px`,
          maxWidth: "none", maxHeight: "none", flex: "none" });
        zoom = s;
      }
      fitPins();
    }
    const measureAgain = () => {
      if (zoom === 1) return;
      base = null;
      drawAt(zoom);
    };
    image.addEventListener("load", measureAgain);
    window.addEventListener("resize", measureAgain);
    pageZoom.attach(imageBox, {
      max: 8,
      scale: () => zoom,
      setScale: drawAt,
      box: () => {
        const r = image.getBoundingClientRect();
        return { left: r.left, top: r.top };
      },
      scrollBy: (x, y) => imageBox.scrollBy({ left: x, top: y, behavior: "instant" }),
      // Not in comment mode, nor on a pin: a pin tapped twice is the pin's.
      doubleTap: (e) => !picking() && !(e.target instanceof Element && e.target.closest(".pin")),
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
    const on = picking();
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
      onChange(false);
    } else if (!page.ready) {
      // Nothing else counts before the script has said it is there.
    } else if (m.type === "anchor" && page.picking && isAnchor(m.anchor)) {
      const { selector, text, x, y, width, screen, hash } = m.anchor;
      const anchor = { type: "element", selector, text, x, y, width };
      // Single-line, as the server wants: names come from the page.
      const one = (s) => s.replace(/[\t\n\r]+/g, " ").trim();
      if (screen && one(screen)) anchor.screen = one(screen);
      if (hash && !/[\t\n\r]/.test(hash)) anchor.hash = hash;
      onSpot(anchor);
    } else if (m.type === "placed" && Array.isArray(m.missing) && m.missing.length <= 500
      && m.missing.every(isKey)) {
      const missing = new Set(m.missing.map(fromFrame));
      const hidden = new Set((Array.isArray(m.hidden) && m.hidden.length <= 500 ? m.hidden : [])
        .filter(isKey).map(fromFrame));
      const same = missing.size === page.missing.size && [...missing].every((k) => page.missing.has(k))
        && hidden.size === page.hidden.size && [...hidden].every((k) => page.hidden.has(k));
      page.missing = missing;
      page.hidden = hidden;
      if (!same) onChange(true);
    } else if (m.type === "pin" && isKey(m.key)) {
      onPin(fromFrame(m.key));
    } else if (m.type === "escape" && page.picking) {
      onEscape();
    } else if (m.type === "blank" && !page.picking
      && (!navigator.userActivation || navigator.userActivation.isActive)) {
      // Only right after a tap in the frame, so the page cannot clear the
      // selection on its own (a click in the frame counts for this page too).
      onBlank();
    } else if (m.type === "open") {
      openLink(m.path, m.fragment);
    }
  }

  // --- Links between files: see static/links.js ----------------------------------
  // The page asks to open another file or project of the service in this
  // tab. Taken only outside comment mode and while this page has a user
  // activation from that click (a click in the frame counts for its
  // parent too), so the content cannot move the tab on its own; the
  // address is built here, on this origin, from a checked path and
  // fragment. Anything else does nothing.
  // The activation must come from the frame: a tap or a key in this page
  // (its buttons, the comment field, the name dialog) gives one too, and a
  // timer in the content could use it. Input here in the last 5 s, about
  // as long as an activation lasts, makes the page's request wait for a
  // click of its own.
  let ownInput = -Infinity;
  // Closing the project's file list (its button, a row or the scrim,
  // Escape) does not count: a link tapped right after it opens at once.
  // The frame can take the focus by itself, so a tap into it cannot be
  // told from that and does not reset this.
  const own = (e) => {
    if (e.type === "keydown" && e.key === "Escape") return;
    if (e.target instanceof Element && e.target.closest("#files-drop, #files-button")) return;
    ownInput = performance.now();
  };
  for (const type of ["pointerdown", "keydown"]) document.addEventListener(type, own, true);
  function openLink(path, fragment) {
    // Comment mode wins, even while its name dialog is open.
    if (picking() || document.body.classList.contains("commenting")) return;
    if (!(navigator.userActivation && navigator.userActivation.isActive)) return;
    if (performance.now() - ownInput < 5000) return;
    const to = pageLinks.destination(path, fragment, location.origin);
    if (to) location.assign(to);
  }

  // Opened at a #fragment: the content is shown at it too. Only a fragment
  // as the links pass it on; a change of it alone (a link to this same file)
  // moves the content without loading it again.
  const raw = frame && frame.getAttribute("src");
  function toFragment() {
    const fragment = pageLinks.fragmentOf(location.hash);
    if (fragment) frame.src = `${raw}#${fragment}`;
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
      onChange(false);
    }, ms);
  }

  if (pinType === "element") {
    toFragment();
    window.addEventListener("hashchange", toFragment);
    window.addEventListener("message", onFrameMessage);
    frame.addEventListener("load", () => {
      // Another document may be in the frame now (a link, a refresh): it
      // has pins only once its script says so.
      page.ready = false;
      page.picking = false;
      page.missing = new Set();
      page.hidden = new Set();
      onChange(false);
      tell({ type: "hello" });
      waitForScript(2000);
    });
    tell({ type: "hello" });
    waitForScript(4000);
  }

  return { page, drawPins, fitPins, reveal, revealSoon, syncPicking };
};
