// The viewer's own controls: the menu of the header's ≡ (a column of square
// buttons fanned out over the dimmed page) and full screen, where a floating
// ≡ takes the header's place. The floating button can be dragged anywhere;
// it snaps to the nearer side edge and this browser keeps its place. A
// double tap on it leaves full screen; a tip beside it says so on the first
// few entries (float-tip.js). The browser's history is never touched: Back belongs to the content. The
// project page uses it too, without comment mode.
(() => {
  const $ = (id) => document.getElementById(id);
  const fan = $("fan");
  if (!fan) return;
  const list = $("fan-list");
  const menuButton = $("menu-button");
  const floatButton = $("float-button");
  const commentsToggle = $("comments-toggle");
  const items = () => [...list.querySelectorAll(".fan-item")].filter((i) => i.getClientRects().length);
  const GAP = 8; // between a button and its menu, and from the window's edges
  const DRAG_PX = 8; // a move shorter than this is a tap
  const LONG_MS = 500; // held this long, still: comment mode on or off
  const PLACE_KEY = "aa-float-button";
  const tip = $("float-tip");

  // --- The menu ---------------------------------------------------------------
  let opener = null;

  function openFan(from) {
    closeTip();
    opener = from;
    fan.hidden = false;
    from.setAttribute("aria-expanded", "true");
    // Comment shows whether comment mode is on, like the header's bubble.
    list.querySelector('[data-act="comment"]')?.setAttribute("aria-checked",
      String(document.body.classList.contains("commenting")));
    place(from);
    items()[0].focus({ preventScroll: true });
  }

  function closeFan(restore = true) {
    if (fan.hidden) return;
    const inside = fan.contains(document.activeElement);
    fan.hidden = true;
    opener.setAttribute("aria-expanded", "false");
    if (restore && inside) opener.focus({ preventScroll: true });
    opener = null;
  }

  // Next to its button: under the header's, above or under the floating one,
  // whichever has room, on the side of the edge the button keeps to.
  function place(from) {
    const box = from.getBoundingClientRect();
    const square = items()[0].querySelector(".fan-sq").getBoundingClientRect().width;
    const inset = (box.width - square) / 2; // squares in line with the button's middle
    const left = from === floatButton && shownSide === "left";
    list.classList.toggle("left", left);
    list.style.left = left ? `${box.left + inset}px` : "auto";
    list.style.right = left ? "auto" : `${window.innerWidth - box.right + inset}px`;
    list.style.top = list.style.bottom = "auto";
    list.style.maxHeight = "none";
    list.classList.remove("scroll");
    const height = list.offsetHeight;
    const below = window.innerHeight - box.bottom - 2 * GAP;
    const above = box.top - 2 * GAP;
    const down = from === menuButton || below >= height || below >= above;
    if (down) list.style.top = `${box.bottom + GAP}px`;
    else list.style.bottom = `${window.innerHeight - box.top + GAP}px`;
    // Scrolls only when it does not fit, since scrolling clips the shadows.
    const room = down ? below : above;
    if (height > room) {
      list.classList.add("scroll");
      list.style.maxHeight = `${room}px`;
    }
  }

  menuButton.addEventListener("click", () => (fan.hidden ? openFan(menuButton) : closeFan()));
  fan.querySelector(".fan-scrim").addEventListener("click", () => closeFan());
  // After the new layout, so the button's box is where it now is.
  window.addEventListener("resize", () => requestAnimationFrame(() => {
    if (!fan.hidden) place(opener);
  }));

  list.addEventListener("click", (e) => {
    const item = e.target.closest(".fan-item");
    if (!item) return;
    const act = item.dataset.act;
    // Open separately is a link and goes on by itself; the project's file
    // list (project.js) and the dialogs (menu-dialogs.js) take the focus
    // themselves and give it back to the button the menu came from.
    const from = opener;
    closeFan(act === "separate");
    if (act === "exit") exitFull();
    else if (act === "files") {
      document.dispatchEvent(new CustomEvent("aa:files", { detail: { from } }));
    }
    else if (act === "comment") commentsToggle.click();
    else if (act === "share" || act === "settings") {
      document.dispatchEvent(new CustomEvent("aa:dialog", { detail: { name: act, from } }));
    }
  });

  // Escape closes the menu before anything else on the page sees it.
  window.addEventListener("keydown", (e) => {
    if (fan.hidden) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopImmediatePropagation();
      closeFan();
    } else if (["ArrowDown", "ArrowUp", "Home", "End"].includes(e.key)) {
      e.preventDefault();
      const all = items();
      const at = all.indexOf(document.activeElement);
      let next;
      if (e.key === "Home") next = 0;
      else if (e.key === "End") next = all.length - 1;
      // From outside the menu (its button), Down goes to the first item and
      // Up to the last.
      else if (at < 0) next = e.key === "ArrowDown" ? 0 : all.length - 1;
      else next = (at + (e.key === "ArrowDown" ? 1 : -1) + all.length) % all.length;
      all[next].focus();
    } else if (e.key === "Tab" && !fan.contains(document.activeElement)) {
      closeFan(false);
    }
  }, true);
  fan.addEventListener("focusout", (e) => {
    if (e.relatedTarget && !fan.contains(e.relatedTarget) && e.relatedTarget !== opener) closeFan(false);
  });

  // Shown by the comments script's toast (see comments.js), the page's one.
  const toast = (message, error = false) => document.dispatchEvent(
    new CustomEvent("aa:toast", { detail: { message, error } }),
  );

  // --- Full screen ------------------------------------------------------------
  const root = document.documentElement;
  const request = root.requestFullscreen || root.webkitRequestFullscreen;

  // Full screen sits in the header too, beside the menu.
  $("full-button").addEventListener("click", () => enterFull());

  function enterFull() {
    document.body.classList.add("full");
    floatButton.hidden = false;
    applyPlace();
    floatButton.focus({ preventScroll: true });
    showTip();
    // Not there on an iPhone: the viewer's own full screen is all it gets.
    if (request) {
      try {
        const asked = request.call(root, { navigationUI: "hide" });
        if (asked && asked.catch) asked.catch(() => {});
      } catch { /* refused: the viewer's own full screen stays */ }
    }
  }

  // Leaving the browser's full screen by itself (Escape, a system gesture)
  // keeps the viewer's: only Exit full screen ends it.
  function exitFull() {
    cancelDrag();
    closeTip();
    lastTap = null;
    document.body.classList.remove("full");
    floatButton.hidden = true;
    const current = document.fullscreenElement || document.webkitFullscreenElement;
    const leave = document.exitFullscreen || document.webkitExitFullscreen;
    if (current && leave) {
      try {
        const left = leave.call(document);
        if (left && left.catch) left.catch(() => {});
      } catch { /* already left */ }
    }
    menuButton.focus({ preventScroll: true });
  }

  // --- The floating button ----------------------------------------------------
  // Its place: the side edge and the height of its middle, as a share of the
  // window's, so it stays in place when the window or the keyboard resizes it.
  let floatSide = "right";
  let floatTop = 0.8;
  try {
    const saved = JSON.parse(localStorage.getItem(PLACE_KEY) || "null");
    if (saved && (saved.side === "left" || saved.side === "right")
        && saved.top >= 0 && saved.top <= 1) {
      floatSide = saved.side;
      floatTop = saved.top;
    }
  } catch { /* no storage: the default place */ }

  // The comments sheet (comments.js) is never covered: the button keeps
  // above its top edge, or takes the top right corner when the sheet fills
  // the height; beside a computer's panel it keeps to the left of it. The
  // place chosen stays as it was, for when the sheet folds again.
  let shownSide = "right";
  function applyPlace() {
    const size = floatButton.offsetHeight || 44;
    let top = floatTop * window.innerHeight - size / 2;
    let side = floatSide;
    let bottom = window.innerHeight - size - GAP;
    let right = GAP;
    const sheet = document.getElementById("comments");
    if (sheet && !sheet.hidden && sheet.getClientRects().length) {
      const box = sheet.getBoundingClientRect();
      if (sheet.dataset.pos === "panel") {
        right = window.innerWidth - box.left + GAP;
      } else if (box.top - size - GAP >= GAP) {
        bottom = box.top - size - GAP;
      } else {
        top = GAP;
        side = "right";
      }
    }
    shownSide = side;
    floatButton.style.top = `${Math.min(Math.max(GAP, top), bottom)}px`;
    floatButton.style.left = side === "left" ? `${GAP}px` : "auto";
    floatButton.style.right = side === "right" ? `${right}px` : "auto";
    placeTip();
  }
  document.addEventListener("aa:sheet", () => { if (!floatButton.hidden && !drag) applyPlace(); });

  function savePlace() {
    try {
      localStorage.setItem(PLACE_KEY, JSON.stringify({ side: floatSide, top: floatTop }));
    } catch { /* kept for this page only */ }
  }

  let drag = null;
  let draggedAt = -Infinity;
  // The last tap on the button, released: {at, x, y}. A press soon after and
  // close to it is the second tap of a double tap, which leaves full screen.
  let lastTap = null;
  const isDouble = (e) => floatTip.isDoubleTap(lastTap,
    { at: performance.now(), x: e.clientX, y: e.clientY });
  let doubled = false; // the second press is still down
  function doubleTap() {
    lastTap = null;
    closeFan(false);
    exitFull();
    // Its click, on letting go, lands on nothing (see swallowUntil), and the
    // content's frame takes no pointer until that click is past, so neither
    // a mouse let go over it nor the click falls through to the content.
    doubled = true;
    swallowUntil = Infinity;
    document.body.classList.add("float-pressed");
  }
  let unpress = 0;
  function releaseDouble() {
    if (!doubled) return;
    doubled = false;
    releaseSoon();
    clearTimeout(unpress);
    unpress = setTimeout(unpressFrame, 350);
  }
  function unpressFrame() {
    if (!drag && !doubled) document.body.classList.remove("float-pressed");
  }
  window.addEventListener("pointerup", releaseDouble, true);
  window.addEventListener("pointercancel", releaseDouble, true);
  // The first tap opened the menu, so the second may land on the menu or
  // its scrim beside the button: a double tap too, and the item under the
  // finger does nothing.
  window.addEventListener("pointerdown", (e) => {
    if (e.button !== 0 || !e.isPrimary || floatButton.hidden || floatButton.contains(e.target) || !isDouble(e)) return;
    e.stopPropagation();
    doubleTap();
  }, true);
  floatButton.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    if (isDouble(e)) {
      doubleTap();
      return;
    }
    const box = floatButton.getBoundingClientRect();
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, dx: e.clientX - box.left,
      dy: e.clientY - box.top, moved: false };
    floatButton.setPointerCapture(e.pointerId);
    // A mouse over the content's frame would move inside the frame instead.
    document.body.classList.add("float-pressed");
    // Held still: comment mode on or off, the button's second action.
    if (!commentsToggle) return;
    const held = drag;
    held.timer = setTimeout(() => {
      if (drag !== held || held.moved) return;
      held.long = true;
      draggedAt = performance.now(); // its click does not open the menu
      closeTip();
      closeFan(false);
      commentsToggle.click();
      // The press's own click, on letting go, does not land on what this
      // opened; see swallowUntil.
      swallowUntil = Infinity; // until released; releaseSoon sets the end
      if (navigator.vibrate) navigator.vibrate(12);
      toast(document.body.classList.contains("commenting") ? "Commenting on" : "Commenting off");
    }, LONG_MS);
  });
  // After a long press, the click its release makes is dropped: while the
  // finger is down and a moment after it is lifted or the press cancelled
  // (release, cancel, lost capture and blur all end it). A time, not a
  // one-off listener, so a cancelled press never leaves a later tap eaten.
  let swallowUntil = -Infinity;
  window.addEventListener("click", (ev) => {
    if (performance.now() > swallowUntil) return;
    swallowUntil = -Infinity;
    ev.stopPropagation();
    ev.preventDefault();
    unpressFrame();
  }, true);
  const releaseSoon = () => {
    if (swallowUntil > performance.now()) swallowUntil = performance.now() + 350;
  };
  // A long press is not the browser's menu.
  floatButton.addEventListener("contextmenu", (e) => e.preventDefault());
  floatButton.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id || drag.long) return;
    if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < DRAG_PX) return;
    if (!drag.moved) {
      clearTimeout(drag.timer);
      drag.moved = true;
      closeFan(false);
      floatButton.classList.add("dragging");
    }
    const size = floatButton.offsetWidth;
    const x = Math.min(Math.max(0, e.clientX - drag.dx), window.innerWidth - size);
    const y = Math.min(Math.max(0, e.clientY - drag.dy), window.innerHeight - size);
    floatButton.style.left = `${x}px`;
    floatButton.style.right = "auto";
    floatButton.style.top = `${y}px`;
    placeTip();
  });
  function endDrag(e) {
    if (!drag || e.pointerId !== drag.id) return;
    clearTimeout(drag.timer);
    if (drag.long) releaseSoon();
    const moved = drag.moved;
    lastTap = moved || drag.long || e.type !== "pointerup" ? null
      : { at: performance.now(), x: e.clientX, y: e.clientY };
    drag = null;
    document.body.classList.remove("float-pressed");
    if (!moved) return;
    draggedAt = performance.now();
    floatButton.classList.remove("dragging");
    const box = floatButton.getBoundingClientRect();
    floatSide = box.left + box.width / 2 < window.innerWidth / 2 ? "left" : "right";
    floatTop = Math.min(1, Math.max(0, (box.top + box.height / 2) / window.innerHeight));
    applyPlace();
    savePlace();
  }
  floatButton.addEventListener("pointerup", endDrag);
  floatButton.addEventListener("pointercancel", endDrag);
  floatButton.addEventListener("lostpointercapture", endDrag);
  // Never leave the content's frame without pointer events.
  function cancelDrag() {
    if (drag && drag.moved && !floatButton.hidden) applyPlace(); // back where it was
    if (drag) clearTimeout(drag.timer);
    if (drag && drag.long) releaseSoon();
    drag = null;
    floatButton.classList.remove("dragging");
    document.body.classList.remove("float-pressed");
  }
  window.addEventListener("blur", () => {
    cancelDrag();
    releaseDouble();
  });
  // A drag ends in a click on some browsers; that one does not open the menu.
  floatButton.addEventListener("click", () => {
    if (performance.now() - draggedAt < 400) return;
    if (fan.hidden) openFan(floatButton);
    else closeFan();
  });
  window.addEventListener("resize", () => { if (!floatButton.hidden) applyPlace(); });

  // --- The tip ----------------------------------------------------------------
  // Shown beside the button on entering full screen, until it has been shown
  // three times or Hide was pressed; OK only closes it. Opening the menu and
  // leaving full screen close it too.
  function showTip() {
    if (!tip) return;
    let raw = null;
    try { raw = localStorage.getItem(floatTip.KEY); } catch { /* no storage: shown each time */ }
    const state = floatTip.read(raw);
    if (!floatTip.due(state)) return;
    try { localStorage.setItem(floatTip.KEY, floatTip.counted(state)); } catch { /* not kept */ }
    tip.hidden = false;
    placeTip();
  }
  function closeTip() {
    if (!tip || tip.hidden) return;
    const inside = tip.contains(document.activeElement);
    tip.hidden = true;
    if (inside && !floatButton.hidden) floatButton.focus({ preventScroll: true });
  }
  function placeTip() {
    if (!tip || tip.hidden) return;
    const box = floatButton.getBoundingClientRect();
    const side = box.left + box.width / 2 < window.innerWidth / 2 ? "left" : "right";
    const at = floatTip.place(box, { width: tip.offsetWidth, height: tip.offsetHeight },
      { width: window.innerWidth, height: window.innerHeight }, side, GAP);
    tip.style.left = `${at.left}px`;
    tip.style.top = `${at.top}px`;
  }
  window.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && tip && !tip.hidden) closeTip();
  });
  if (tip) {
    $("float-tip-ok").addEventListener("click", closeTip);
    $("float-tip-hide").addEventListener("click", () => {
      try { localStorage.setItem(floatTip.KEY, floatTip.hidden()); } catch { /* this page only */ }
      closeTip();
    });
  }
})();
