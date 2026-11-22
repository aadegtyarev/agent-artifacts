// The viewer's own controls: the menu of the header's ≡ (a column of square
// buttons fanned out over the dimmed page) and full screen, where a floating
// ≡ takes the header's place. The floating button can be dragged anywhere;
// it snaps to the nearer side edge and this browser keeps its place. The
// browser's history is never touched: Back belongs to the content.
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
  const PLACE_KEY = "aa-float-button";

  // --- The menu ---------------------------------------------------------------
  let opener = null;

  function openFan(from) {
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
    if (act === "theme") {
      nextTheme();
      return;
    }
    // Download and Open separately are links and go on by themselves.
    closeFan(act === "download" || act === "separate" || act === "copy");
    if (act === "full") enterFull();
    else if (act === "exit") exitFull();
    else if (act === "comment") commentsToggle.click();
    else if (act === "copy") copyLink(item.dataset.url);
  });

  // Theme: Auto (the system's), Light, Dark, in turn; kept in this browser.
  // theme.js applies it before the page is drawn.
  const THEMES = ["auto", "light", "dark"];
  const themeLabel = $("theme-label");
  const theme = () => document.documentElement.dataset.theme || "auto";
  const showTheme = () => {
    const t = theme();
    themeLabel.textContent = `Theme: ${t[0].toUpperCase()}${t.slice(1)}`;
  };
  function nextTheme() {
    const t = THEMES[(THEMES.indexOf(theme()) + 1) % THEMES.length];
    if (t === "auto") delete document.documentElement.dataset.theme;
    else document.documentElement.dataset.theme = t;
    try {
      if (t === "auto") localStorage.removeItem("aa-theme");
      else localStorage.setItem("aa-theme", t);
    } catch {
      // No storage: the theme lasts while the page is open.
    }
    showTheme();
  }
  showTheme();

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

  // --- Copy link --------------------------------------------------------------
  // Shown by the comments script's toast (see comments.js), the page's one.
  const toast = (message, error = false) => document.dispatchEvent(
    new CustomEvent("aa:toast", { detail: { message, error } }),
  );

  function copyLink(url) {
    const copied = navigator.clipboard
      ? navigator.clipboard.writeText(url)
      : Promise.reject(new Error("no clipboard"));
    copied.then(
      () => toast("Link copied"),
      () => toast(`Could not copy. The link: ${url}`, true),
    );
  }

  // --- Full screen ------------------------------------------------------------
  const root = document.documentElement;
  const request = root.requestFullscreen || root.webkitRequestFullscreen;

  function enterFull() {
    document.body.classList.add("full");
    floatButton.hidden = false;
    applyPlace();
    floatButton.focus({ preventScroll: true });
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
  }
  document.addEventListener("aa:sheet", () => { if (!floatButton.hidden && !drag) applyPlace(); });

  function savePlace() {
    try {
      localStorage.setItem(PLACE_KEY, JSON.stringify({ side: floatSide, top: floatTop }));
    } catch { /* kept for this page only */ }
  }

  let drag = null;
  let draggedAt = -Infinity;
  floatButton.addEventListener("pointerdown", (e) => {
    if (e.button !== 0) return;
    const box = floatButton.getBoundingClientRect();
    drag = { id: e.pointerId, x: e.clientX, y: e.clientY, dx: e.clientX - box.left,
      dy: e.clientY - box.top, moved: false };
    floatButton.setPointerCapture(e.pointerId);
    // A mouse over the content's frame would move inside the frame instead.
    document.body.classList.add("float-pressed");
  });
  floatButton.addEventListener("pointermove", (e) => {
    if (!drag || e.pointerId !== drag.id) return;
    if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) < DRAG_PX) return;
    if (!drag.moved) {
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
  });
  function endDrag(e) {
    if (!drag || e.pointerId !== drag.id) return;
    const moved = drag.moved;
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
    drag = null;
    floatButton.classList.remove("dragging");
    document.body.classList.remove("float-pressed");
  }
  window.addEventListener("blur", cancelDrag);
  // A drag ends in a click on some browsers; that one does not open the menu.
  floatButton.addEventListener("click", () => {
    if (performance.now() - draggedAt < 400) return;
    if (fan.hidden) openFan(floatButton);
    else closeFan();
  });
  window.addEventListener("resize", () => { if (!floatButton.hidden) applyPlace(); });
})();
