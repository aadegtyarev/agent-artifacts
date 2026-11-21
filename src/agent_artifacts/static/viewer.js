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
    document.body.classList.add("fan-open");
    place(from);
    items()[0].focus({ preventScroll: true });
  }

  function closeFan(restore = true) {
    if (fan.hidden) return;
    const inside = fan.contains(document.activeElement);
    fan.hidden = true;
    document.body.classList.remove("fan-open");
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
    const left = from === floatButton && floatSide === "left";
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
  window.addEventListener("resize", () => { if (!fan.hidden) place(opener); });

  list.addEventListener("click", (e) => {
    const item = e.target.closest(".fan-item");
    if (!item) return;
    const act = item.dataset.act;
    // Download and Open separately are links and go on by themselves.
    closeFan(act === "download" || act === "separate" || act === "copy");
    if (act === "full") enterFull();
    else if (act === "exit") exitFull();
    else if (act === "comment") commentsToggle.click();
    else if (act === "copy") copyLink(item.dataset.url);
  });

  // Escape closes the menu before anything else on the page sees it.
  window.addEventListener("keydown", (e) => {
    if (fan.hidden) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopImmediatePropagation();
      closeFan();
    } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      const all = items();
      const at = all.indexOf(document.activeElement);
      const step = e.key === "ArrowDown" ? 1 : -1;
      all[(at + step + all.length) % all.length].focus();
    } else if (e.key === "Tab" && !fan.contains(document.activeElement)) {
      closeFan(false);
    }
  }, true);
  fan.addEventListener("focusout", (e) => {
    if (e.relatedTarget && !fan.contains(e.relatedTarget) && e.relatedTarget !== opener) closeFan(false);
  });

  // --- Copy link --------------------------------------------------------------
  const toastBox = $("toast");
  let toastTimer = 0;
  function toast(text, error = false) {
    clearTimeout(toastTimer);
    $("toast-text").textContent = text;
    $("toast-undo").hidden = true;
    toastBox.classList.toggle("error", error);
    toastBox.classList.add("on");
    toastTimer = setTimeout(() => toastBox.classList.remove("on"), error ? 10000 : 2500);
  }

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

  function applyPlace() {
    const size = floatButton.offsetHeight || 44;
    const top = floatTop * window.innerHeight - size / 2;
    floatButton.style.top = `${Math.min(Math.max(GAP, top), window.innerHeight - size - GAP)}px`;
    floatButton.style.left = floatSide === "left" ? `${GAP}px` : "auto";
    floatButton.style.right = floatSide === "right" ? `${GAP}px` : "auto";
  }

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
  // A drag ends in a click on some browsers; that one does not open the menu.
  floatButton.addEventListener("click", () => {
    if (performance.now() - draggedAt < 400) return;
    if (fan.hidden) openFan(floatButton);
    else closeFan();
  });
  window.addEventListener("resize", () => { if (!floatButton.hidden) applyPlace(); });
})();
