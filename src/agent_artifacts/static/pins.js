// Pins on a page. /a/{id}/raw appends this to HTML and includes it in the
// Markdown and text pages; it runs inside the sandbox, draws the pins in its
// own layer over the page and talks to the viewer page (its parent) only by
// postMessage. It gets numbers, anchors and resolved flags, never comment
// texts or names. In pin mode (while the viewer's comments are open) it takes
// each click and cancels it, so a link or a button under it does not fire,
// and answers with its anchor.
(() => {
  const viewer = window.parent;
  if (viewer === window) return;
  const tell = (m) => viewer.postMessage(m, "*");

  // A closed shadow root: the page's styles and scripts cannot reach in,
  // and none of the page's own elements is touched.
  const host = document.createElement("div");
  host.style.cssText = "all:initial;position:fixed;left:0;top:0;width:100%;height:100%;"
    + "z-index:2147483647;pointer-events:none;overflow:hidden";
  const root = host.attachShadow({ mode: "closed" });
  root.innerHTML = "<style>"
    + ".box{position:absolute;display:none;box-sizing:border-box;border-radius:4px;pointer-events:none}"
    + ".hover{border:2px dashed #0a64d8;background:rgb(10 100 216/.07)}"
    + ".draft{border:2px dashed #6e6e73;background:rgb(110 110 115/.08)}"
    + ".pin{position:absolute;box-sizing:border-box;margin:0;transform:translateY(-100%);min-width:24px;"
    + "height:24px;padding:0 6px;font:600 12px/20px system-ui,sans-serif;color:#fff;background:#0a64d8;"
    + "border:2px solid #fff;border-radius:12px 12px 12px 2px;box-shadow:0 1px 4px rgb(0 0 0/.4);"
    + "pointer-events:auto;cursor:pointer}"
    + "@media (max-width:640px){.pin::before{content:'';position:absolute;inset:-12px}}"
    + ".pin.left{transform:translate(-100%,-100%);border-radius:12px 12px 2px 12px}"
    + ".pin.down{transform:none;border-radius:2px 12px 12px 12px}"
    + ".pin.left.down{transform:translateX(-100%);border-radius:12px 2px 12px 12px}"
    + ".pin.resolved{background:#8e8e93}.pin.new{background:#6e6e73}"
    + ".pin.lit{z-index:2;color:#1d1d1f;background:#ffb020;animation:p 1s ease-out 2}"
    + "@keyframes p{from{box-shadow:0 0 0 0 rgb(255 176 32/.8)}to{box-shadow:0 0 0 14px rgb(255 176 32/0)}}"
    + "@media (prefers-reduced-motion:reduce){.pin.lit{animation:none}}"
    + "</style><div class='box hover'></div><div class='box draft'></div><div class='pins'></div>";
  const hoverBox = root.querySelector(".hover");
  const draftBox = root.querySelector(".draft");
  const layer = root.querySelector(".pins");
  document.documentElement.append(host);

  let picking = false;
  let pins = [];
  let draft = null;
  let hovered = null;
  let reported = "";
  // Elements found for anchors, kept while scrolling; dropped when the
  // page's size or content changes.
  let found = new Map();
  // The pin buttons drawn, by comment number (0: the draft), kept across
  // layouts so a pin under the pointer stays the same node.
  const drawn = new Map();
  // Code points, as the server counts them.
  const length = (s) => [...s].length;

  // The element a person points at: the nearest link, button or field, an
  // <svg> as a whole, else the nearest box that is not inline text.
  const INTERACTIVE = "a[href],button,input,select,textarea,label,summary,[role=button],[role=link]";
  function pick(target) {
    let el = target && target.nodeType === 1 ? target : target && target.parentElement;
    if (!el || !document.body.contains(el)) return document.body;
    let svg = el.closest("svg");
    while (svg && svg.ownerSVGElement) svg = svg.ownerSVGElement;
    if (svg) return svg;
    const control = el.closest(INTERACTIVE);
    if (control) return control;
    while (el !== document.body && el.parentElement && getComputedStyle(el).display === "inline") {
      el = el.parentElement;
    }
    return el;
  }

  // A selector that matches only this element: its #id, or a path of tags
  // with up to two stable classes and :nth-of-type where needed, from the
  // nearest ancestor with an id, cut from the left while it stays unique.
  const esc = (s) => CSS.escape(s);
  const goodId = (id) => id && id.length <= 64 && !/\d{4,}|^\d|:/.test(id);
  const goodClass = (c) => /^[A-Za-z][\w-]{0,40}$/.test(c) && !/\d{3,}/.test(c)
    && !/^(is-|has-|js-)|(^|-)(active|hover|focus|open|selected|show|shown|hidden|visible|current|disabled)$/.test(c);
  const only = (selector, el) => {
    try {
      const all = document.querySelectorAll(selector);
      return all.length === 1 && all[0] === el;
    } catch { return false; }
  };
  const uniqueId = (el) => goodId(el.id) && only(`#${esc(el.id)}`, el);

  function step(el) {
    let s = esc(el.localName);
    const classes = [...el.classList].filter(goodClass).slice(0, 2).map(esc);
    if (classes.length) s += `.${classes.join(".")}`;
    const siblings = el.parentElement ? [...el.parentElement.children] : [];
    const matches = (x) => { try { return x.matches(s); } catch { return true; } };
    if (siblings.some((x) => x !== el && matches(x))) {
      s += `:nth-of-type(${siblings.filter((x) => x.localName === el.localName).indexOf(el) + 1})`;
    }
    return s;
  }

  function selectorOf(el) {
    if (uniqueId(el)) return `#${esc(el.id)}`;
    const parts = [];
    for (let node = el; node && node !== document.documentElement; node = node.parentElement) {
      if (node === document.body) { parts.unshift("body"); break; }
      if (node !== el && uniqueId(node)) { parts.unshift(`#${esc(node.id)}`); break; }
      parts.unshift(step(node));
    }
    let best = parts.join(" > ");
    for (let i = 1; i < parts.length && only(parts.slice(i).join(" > "), el); i++) {
      best = parts.slice(i).join(" > ");
    }
    // Too long to keep: the text alone finds it again.
    return length(best) <= 512 ? best : "";
  }

  // One line without control or bidi characters, at most `n` code points.
  const clean = (s, n) => [...(s || "").replace(/[\u0000-\u001f\u007f-\u009f\u2028\u2029\u202a-\u202e\u2066-\u2069]+/g, " ")
    .replace(/\s+/g, " ").trim()].slice(0, n).join("");

  function textOf(el) {
    const t = el.matches("input,select,textarea") ? el.placeholder || "" : el.innerText;
    return clean(t || el.getAttribute("aria-label") || el.getAttribute("alt") || el.title || "", 200);
  }

  // The selector first; if it matches nothing, the first element whose
  // visible text starts with the anchor's text, the innermost such.
  function find(anchor) {
    let el = null;
    try { el = anchor.selector ? document.querySelector(anchor.selector) : null; } catch { /* a bad selector */ }
    if (el === host) el = null;
    const want = anchor.text;
    if (!el && want) {
      const starts = (x) => x !== host && x.textContent.replace(/\s+/g, " ").includes(want)
        && textOf(x).startsWith(want);
      el = [...document.body.querySelectorAll("*")].find(starts) || null;
      for (let inner = el; inner; inner = [...inner.children].find(starts)) el = inner;
    }
    return el && el.getClientRects().length ? el : null;
  }

  function located(anchor, key) {
    if (!found.has(key)) found.set(key, find(anchor));
    const el = found.get(key);
    if (!el || !el.isConnected) return null;
    const r = el.getBoundingClientRect();
    return { el, r, x: r.left + anchor.x * r.width, y: r.top + anchor.y * r.height };
  }

  function outline(box, el) {
    const r = el && el.getBoundingClientRect();
    box.style.display = r && (r.width || r.height) ? "block" : "none";
    if (box.style.display === "none") return;
    box.style.left = `${r.left - 3}px`;
    box.style.top = `${r.top - 3}px`;
    box.style.width = `${r.width + 6}px`;
    box.style.height = `${r.height + 6}px`;
  }

  // The pin `key` at (x, y): an existing button is moved, and only what
  // changed is set, so a lit pin's pulse does not start over.
  function pinAt(key, label, x, y, ...classes) {
    let b = drawn.get(key);
    if (!b) {
      b = document.createElement("button");
      b.type = "button";
      b.dataset.key = key;
      layer.append(b);
      drawn.set(key, b);
    }
    if (x + 26 > window.innerWidth) classes.push("left");
    if (y < 26) classes.push("down");
    const className = ["pin", ...classes].filter(Boolean).join(" ");
    if (b.className !== className) b.className = className;
    if (b.textContent !== label) b.textContent = label;
    if (b.style.left !== `${x}px`) b.style.left = `${x}px`;
    if (b.style.top !== `${y}px`) b.style.top = `${y}px`;
  }

  // Draws the pins where their elements are now and tells the viewer which
  // were not found and where the draft pin is.
  function layout() {
    const shown = new Set();
    const missing = [];
    for (const p of pins) {
      const at = located(p.anchor, p.n);
      if (!at) { missing.push(p.n); continue; }
      pinAt(p.n, String(p.n), at.x, at.y, p.resolved && "resolved", p.lit && "lit");
      shown.add(p.n);
    }
    let spot = null;
    const d = draft && located(draft, "draft");
    if (d) {
      pinAt(0, "+", d.x, d.y, "new");
      shown.add(0);
      spot = { x: Math.round(d.x), y: Math.round(d.y) };
    }
    for (const [key, b] of drawn) {
      if (!shown.has(key)) { b.remove(); drawn.delete(key); }
    }
    outline(draftBox, d && d.el);
    outline(hoverBox, picking && hovered);
    const report = JSON.stringify({ missing, spot });
    if (report !== reported) {
      reported = report;
      tell({ type: "placed", missing, spot });
    }
  }

  let queued = false;
  function schedule(again = false) {
    if (again) found = new Map();
    if (queued) return;
    queued = true;
    requestAnimationFrame(() => { queued = false; layout(); });
  }

  // Pin mode: the presses around a click stop here too, so menus that
  // open on a press stay shut; scrolling is left alone.
  // The layer's shadow root is closed: from out here its pins are the host.
  const fromLayer = (e) => e.target === host;
  function swallow(e) {
    if (!picking || fromLayer(e)) return;
    e.stopImmediatePropagation();
    if (e.type === "mousedown" || e.type === "dblclick" || e.type === "auxclick") e.preventDefault();
  }
  for (const type of ["pointerdown", "mousedown", "pointerup", "mouseup", "touchend", "dblclick", "auxclick"]) {
    window.addEventListener(type, swallow, true);
  }
  window.addEventListener("click", (e) => {
    if (!picking || fromLayer(e)) return;
    e.stopImmediatePropagation();
    e.preventDefault();
    hovered = null;
    const el = pick(e.target);
    const r = el.getBoundingClientRect();
    // A keyboard "click" has no point: the middle of the element.
    const keyboard = e.detail === 0 && !e.clientX && !e.clientY;
    const fraction = (v, size) => (keyboard || !size ? 0.5 : Math.round(Math.min(1, Math.max(0, v / size)) * 1000) / 1000);
    const anchor = {
      type: "element",
      selector: selectorOf(el),
      text: textOf(el),
      x: fraction(e.clientX - r.left, r.width),
      y: fraction(e.clientY - r.top, r.height),
      width: Math.max(1, Math.round(window.innerWidth)),
    };
    tell({ type: "anchor", anchor, spot: {
      x: Math.round(r.left + anchor.x * r.width), y: Math.round(r.top + anchor.y * r.height),
    } });
    schedule();
  }, true);
  window.addEventListener("pointermove", (e) => {
    if (!picking || e.pointerType !== "mouse") return;
    const el = fromLayer(e) ? null : pick(e.target);
    if (el !== hovered) { hovered = el; outline(hoverBox, el); }
  }, { capture: true, passive: true });
  document.addEventListener("mouseleave", () => { hovered = null; outline(hoverBox, null); });

  // A pin: its comment opens in the viewer. On a phone a pin's tap area is
  // larger than the pin, so the pin nearest to the finger wins.
  layer.addEventListener("click", (e) => {
    const hit = e.target.closest(".pin");
    if (!hit) return;
    e.preventDefault();
    e.stopPropagation();
    let best = hit;
    let distance = Infinity;
    for (const pin of e.detail ? drawn.values() : []) {
      const r = pin.getBoundingClientRect();
      const d = (r.left + r.width / 2 - e.clientX) ** 2 + (r.top + r.height / 2 - e.clientY) ** 2;
      if (d < distance) { distance = d; best = pin; }
    }
    tell({ type: "pin", n: Number(best.dataset.key) });
  });

  // Escape in pin mode reaches the viewer too while the focus is in the page.
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && picking) tell({ type: "escape" });
  }, true);

  // Scrolls the pin's point, not just its element, to the middle: inside
  // any scrolling box around it, then in the window.
  function reveal(anchor) {
    const el = find(anchor);
    if (!el) return;
    const point = (box) => {
      const r = el.getBoundingClientRect();
      return { top: r.top + anchor.y * r.height - box.top - box.height / 2,
        left: r.left + anchor.x * r.width - box.left - box.width / 2 };
    };
    for (let box = el.parentElement; box && box !== document.body; box = box.parentElement) {
      const scrolls = box.scrollHeight > box.clientHeight || box.scrollWidth > box.clientWidth;
      if (scrolls && /auto|scroll/.test(getComputedStyle(box).overflow)) box.scrollBy(point(box.getBoundingClientRect()));
    }
    const smooth = !window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    window.scrollBy({
      ...point({ top: 0, left: 0, height: window.innerHeight, width: window.innerWidth }),
      behavior: smooth ? "smooth" : "auto",
    });
  }

  // What the viewer sends is checked like anything from outside.
  const isAnchor = (a) => a && typeof a === "object" && a.type === "element"
    && typeof a.selector === "string" && length(a.selector) <= 512
    && typeof a.text === "string" && length(a.text) <= 200
    && [a.x, a.y].every((v) => typeof v === "number" && v >= 0 && v <= 1);
  window.addEventListener("message", (e) => {
    if (e.source !== viewer) return;
    const m = e.data;
    if (!m || typeof m !== "object") return;
    if (m.type === "hello") {
      // The viewer starts over: it gets the next report even if unchanged.
      reported = "";
      tell({ type: "ready" });
    } else if (m.type === "mode") {
      picking = m.on === true;
      hovered = null;
      schedule();
    } else if (m.type === "pins" && Array.isArray(m.pins) && m.pins.length <= 200) {
      pins = m.pins.filter((p) => p && Number.isInteger(p.n) && p.n > 0 && isAnchor(p.anchor))
        .map((p) => ({ n: p.n, anchor: p.anchor, resolved: p.resolved === true, lit: p.lit === true }));
      draft = isAnchor(m.draft) ? m.draft : null;
      schedule(true);
    } else if (m.type === "show" && Number.isInteger(m.n)) {
      // 0 is the draft pin.
      const anchor = m.n === 0 ? draft : (pins.find((p) => p.n === m.n) || {}).anchor;
      if (anchor) reveal(anchor);
    }
  });
  window.addEventListener("scroll", () => schedule(), { capture: true, passive: true });
  window.addEventListener("resize", () => schedule(true));
  new ResizeObserver(() => schedule(true)).observe(document.documentElement);
  new MutationObserver(() => schedule(true)).observe(document.documentElement, {
    subtree: true, childList: true, attributes: true, characterData: true,
  });
  tell({ type: "ready" });
})();
