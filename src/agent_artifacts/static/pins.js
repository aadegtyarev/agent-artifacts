// Pins on a page. /a/{id}/raw appends this to HTML and includes it in the
// Markdown and text pages; it runs inside the sandbox, draws the pins in its
// own layer over the page and talks to the viewer page (its parent) only by
// postMessage. It gets keys, short labels (a number or a letter), anchors and
// styles, never comment texts or names. In pin mode (the viewer's comment
// mode) it takes each click and cancels it, so a link or a button under it
// does not fire, and answers with its anchor.
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
  // The same pins as on an image in the viewer (site.css). Colour: whose
  // (blue this browser, orange others). Fill: the stage (dashed hollow a
  // draft, an outline awaiting approval, solid in force, grey rejected).
  // One badge at the corner: the agent's state or the person's decision.
  root.innerHTML = "<style>"
    + ".box{position:absolute;display:none;box-sizing:border-box;border-radius:4px;pointer-events:none}"
    + ".hover{border:2px dashed #0a84ff;background:rgb(10 132 255/.07)}"
    + ".fresh{border:1.5px dashed #0a84ff;background:rgb(10 132 255/.05)}"
    + ".pin{--c:#e8890c;position:absolute;box-sizing:border-box;margin:0;padding:0;transform:translateY(-100%);"
    + "display:flex;align-items:center;justify-content:center;width:26px;height:26px;"
    + "font:600 13px/1 -apple-system,system-ui,Roboto,sans-serif;color:#fff;background:var(--c);border:0;"
    + "border-radius:50% 50% 50% 4px;box-shadow:0 0 0 2px #fff,0 2px 6px rgb(0 0 0/.28);"
    + "pointer-events:auto;cursor:pointer;-webkit-tap-highlight-color:transparent}"
    + ".pin::before{content:'';position:absolute;inset:-10px}"
    + ".pin.own{--c:#0a84ff}"
    + ".pin.draft,.pin.pend{color:var(--c);background:#fff;border:2px dashed var(--c);box-shadow:0 2px 6px rgb(0 0 0/.2)}"
    + ".pin.pend{border-style:solid}"
    + ".pin.rej{background:#c7c7cc}"
    + ".pin.left{transform:translate(-100%,-100%);border-radius:50% 50% 4px 50%}"
    + ".pin.down{transform:none;border-radius:4px 50% 50% 50%}"
    + ".pin.left.down{transform:translateX(-100%);border-radius:50% 4px 50% 50%}"
    + ".pin.lit{z-index:2;width:32px;height:32px;font-size:15px;box-shadow:0 0 0 2px #fff,0 0 0 6px rgb(28 28 30/.18),0 2px 6px rgb(0 0 0/.25)}"
    + ".pin.draft.lit,.pin.pend.lit{box-shadow:0 0 0 6px rgb(28 28 30/.18),0 2px 6px rgb(0 0 0/.25)}"
    + ".bdg{position:absolute;right:-10px;top:-10px;width:20px;height:20px;border-radius:50%;"
    + "box-shadow:0 0 0 2px #fff;display:flex;align-items:center;justify-content:center;pointer-events:none}"
    + ".bdg svg{width:12px;height:12px;display:block}"
    + ".bdg.wait{background:#8e8e93}.bdg.work{background:#5e5ce6}.bdg.fin,.bdg.wrote{background:#34c759}"
    + ".bdg.ask{background:#ff9f0a}.bdg.ok,.bdg.re{background:#0a84ff}.bdg.no{background:#636366}"
    + ".bdg.work::after{content:'';position:absolute;inset:-5px;border-radius:50%;border:2px solid rgb(94 92 230/.4)}"
    + ".bdg.new{right:-4px;top:-4px;width:12px;height:12px;background:#ff3b30}"
    + "</style><div class='box hover'></div><div class='box fresh'></div><div class='pins'></div>";
  const hoverBox = root.querySelector(".hover");
  const draftBox = root.querySelector(".fresh");
  const layer = root.querySelector(".pins");
  document.documentElement.append(host);

  let picking = false;
  // {key, label, anchor, own, draft, lit}; the key "new" is the pin of the
  // draft being written, its element outlined.
  let pins = [];
  let hovered = null;
  let reported = "";
  // Elements found for anchors, kept while scrolling; dropped when the
  // page's size or content changes.
  let found = new Map();
  // The pin buttons drawn, by key, kept across
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

  // The screen an element is on, by name. Its blocks (section, article,
  // dialog, region or labelled block, not main and not a control) from the
  // element up; the screen is the outermost one that sits beside another
  // such block (one of several screens), else the outermost one. Named by
  // its aria-label, else its own first heading, else its id; else the
  // page's title.
  const BLOCK = "section,article,dialog,[role=dialog],[role=region],[aria-label]";
  const CONTROL = "a,button,input,select,textarea,label,summary,[role=button],[role=link],svg,img";
  const isBlock = (x) => x.matches(BLOCK) && !x.matches(CONTROL) && !x.matches("main,[role=main]");
  function screenOf(el) {
    let outer = null;
    let beside = null;
    for (let x = el; x && x !== document.body; x = x.parentElement) {
      if (!isBlock(x)) continue;
      outer = x;
      if (x.parentElement && [...x.parentElement.children].some((y) => y !== x && isBlock(y))) beside = x;
    }
    const screen = beside || outer;
    let name = "";
    if (screen) {
      // Its own heading: not one inside a nested section, article or dialog.
      const NEST = "section,article,dialog,[role=dialog],[role=region]";
      const h = [...screen.querySelectorAll("h1,h2,h3")].find((x) => {
        const n = x.closest(NEST);
        return !n || n === screen || !screen.contains(n);
      });
      name = screen.getAttribute("aria-label") || (h && h.textContent) || screen.id || "";
    }
    return clean(name || document.title || "", 80);
  }
  // The page's #hash as kept with an anchor: "#" for none (the start screen).
  const hashNow = () => location.hash || "#";
  const isHash = (h) => typeof h === "string" && h.length <= 200 && /^#[\x21-\x7e]*$/.test(h);

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
    return el;
  }
  const visible = (el) => Boolean(el && el.getClientRects().length);

  function located(anchor, key) {
    if (!found.has(key)) found.set(key, find(anchor));
    const el = found.get(key);
    if (!el || !el.isConnected || !visible(el)) return null;
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
  // Badge marks, the same as in comments.js.
  const MARK = {
    star: "M6 .6C6.45 4 8 5.55 11.4 6 8 6.45 6.45 8 6 11.4 5.55 8 4 6.45.6 6 4 5.55 5.55 4 6 .6z",
    check: "M2.4 6.3l2.4 2.4L9.6 3.6",
    ask: "M4 4.3a2 2 0 1 1 2.9 1.8c-.6.3-.9.7-.9 1.3v.4M6 9.6v.2",
    reply: "M4.8 2.8L2 5.6l2.8 2.8M2.3 5.6h5a2.6 2.6 0 0 1 2.6 2.6v1",
    cross: "M3 3l6 6M9 3L3 9",
  };
  const BADGES = { wait: "star", work: "star", fin: "star", wrote: "ask", ask: "ask", ok: "check", re: "reply", no: "cross", new: "" };
  const FILLS = ["draft", "pend", "solid", "rej"];
  function badge(name) {
    const span = document.createElement("span");
    span.className = `bdg ${name}`;
    const mark = BADGES[name];
    if (!mark) return span;
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 12 12");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", MARK[mark]);
    if (mark === "star") path.setAttribute("fill", "#fff");
    else {
      path.setAttribute("fill", "none");
      path.setAttribute("stroke", "#fff");
      path.setAttribute("stroke-width", "2");
      path.setAttribute("stroke-linecap", "round");
      path.setAttribute("stroke-linejoin", "round");
    }
    svg.append(path);
    span.append(svg);
    return span;
  }

  function pinAt(key, label, badgeName, x, y, ...classes) {
    let b = drawn.get(key);
    if (!b) {
      b = document.createElement("button");
      b.type = "button";
      b.dataset.key = key;
      layer.append(b);
      drawn.set(key, b);
    }
    // By the selected pin's size (32px), so a pin keeps its side when selected.
    if (x + 32 > window.innerWidth) classes.push("left");
    if (y < 32) classes.push("down");
    const className = ["pin", ...classes].filter(Boolean).join(" ");
    if (b.className !== className) b.className = className;
    if (b.dataset.label !== label || b.dataset.badge !== badgeName) {
      b.dataset.label = label;
      b.dataset.badge = badgeName;
      b.replaceChildren(label);
      if (badgeName) b.append(badge(badgeName));
    }
    if (b.style.left !== `${x}px`) b.style.left = `${x}px`;
    if (b.style.top !== `${y}px`) b.style.top = `${y}px`;
  }

  // Draws the pins where their elements are now and tells the viewer which
  // were not found.
  function layout() {
    const shown = new Set();
    const missing = [];
    // Of the missing, those whose element is there but not shown.
    const hidden = [];
    let fresh = null;
    for (const p of pins) {
      const at = located(p.anchor, p.key);
      if (!at) {
        missing.push(p.key);
        const el = found.get(p.key);
        // Its element is here but not shown, or it was on another #screen.
        const elsewhere = isHash(p.anchor.hash) && hashNow() !== p.anchor.hash;
        if ((el && el.isConnected) || elsewhere) hidden.push(p.key);
        continue;
      }
      pinAt(p.key, p.label, p.badge, at.x, at.y, p.own && "own", p.fill !== "solid" && p.fill, p.lit && "lit");
      shown.add(p.key);
      if (p.key === "new") fresh = at.el;
    }
    for (const [key, b] of drawn) {
      if (!shown.has(key)) { b.remove(); drawn.delete(key); }
    }
    outline(draftBox, fresh);
    outline(hoverBox, picking && hovered);
    const report = JSON.stringify([missing, hidden]);
    if (report !== reported) {
      reported = report;
      tell({ type: "placed", missing, hidden });
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
    const screen = screenOf(el);
    if (screen) anchor.screen = screen;
    // Kept only whole: a cut one would never match the page's again.
    if (isHash(hashNow())) anchor.hash = hashNow();
    tell({ type: "anchor", anchor });
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
    tell({ type: "pin", key: best.dataset.key });
  });

  // Escape in pin mode reaches the viewer too while the focus is in the page.
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && picking) tell({ type: "escape" });
  }, true);

  // Scrolls the pin's point, not just its element, to the middle: inside
  // any scrolling box around it, then in the window.
  function reveal(anchor) {
    const el = find(anchor);
    if (!visible(el)) return;
    const point = (box) => {
      const r = el.getBoundingClientRect();
      return { top: r.top + anchor.y * r.height - box.top - box.height / 2,
        left: r.left + anchor.x * r.width - box.left - box.width / 2 };
    };
    for (let box = el.parentElement; box && box !== document.documentElement; box = box.parentElement) {
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
  const isKey = (k) => typeof k === "string" && /^[A-Za-z0-9_-]{1,24}$/.test(k);
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
    } else if (m.type === "pins" && Array.isArray(m.pins) && m.pins.length <= 500) {
      pins = m.pins.filter((p) => p && isKey(p.key) && typeof p.label === "string"
        && length(p.label) <= 3 && isAnchor(p.anchor))
        .map((p) => ({ key: p.key, label: p.label, anchor: p.anchor, own: p.own === true,
          fill: FILLS.includes(p.fill) ? p.fill : "solid",
          badge: Object.hasOwn(BADGES, p.badge) ? p.badge : "", lit: p.lit === true }));
      schedule(true);
    } else if (m.type === "show" && isKey(m.key)) {
      const pin = pins.find((p) => p.key === m.key);
      if (!pin) return;
      // On another screen with an address: go there first, then show it once
      // the page has drawn that screen.
      if (!visible(find(pin.anchor)) && isHash(pin.anchor.hash) && hashNow() !== pin.anchor.hash) {
        // Replaced, not pushed: Back leaves the viewer, not the page's screens.
        // From this page's own address, not a relative one: a <base href>
        // would take the frame elsewhere.
        const to = new URL(location.href);
        to.hash = pin.anchor.hash;
        location.replace(to.href);
        // Shown once the page has drawn that screen: tried for up to a second.
        let tries = 0;
        const later = () => {
          found = new Map();
          layout();
          if (visible(find(pin.anchor))) reveal(pin.anchor);
          else if (++tries < 10) setTimeout(later, 100);
        };
        setTimeout(later, 60);
        return;
      }
      reveal(pin.anchor);
    }
  });
  window.addEventListener("scroll", () => schedule(), { capture: true, passive: true });
  window.addEventListener("resize", () => schedule(true));
  // A screen shown by :target changes nothing in the DOM.
  window.addEventListener("hashchange", () => schedule(true));
  new ResizeObserver(() => schedule(true)).observe(document.documentElement);
  new MutationObserver(() => schedule(true)).observe(document.documentElement, {
    subtree: true, childList: true, attributes: true, characterData: true,
  });
  tell({ type: "ready" });
})();
