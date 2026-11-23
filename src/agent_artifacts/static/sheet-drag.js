// The comments sheet on a phone: tap and drag. Loaded as its own script
// before comments.js, which calls this once with the sheet's parts and says
// what a height or a tap means for what the sheet shows:
//   pos():          the sheet's height now: strip, m (content), f (full)
//   open():         a drag from the strip has begun: the sheet opens
//   snap(pos):      a drag ended nearest to height `pos`
//   tap(onGrip):    a tap on the grip (true) or on the title line (false)
// Dragging the grip, the title line (its buttons too) or the strip moves the
// sheet with the finger; let go, it snaps to the height nearest to where it
// is. A drag changes the height only; what a tap does is the caller's.
const sheetDrag = ({ sheet, strip, grip, desk, pos, open, snap, tap }) => {
  const POS = ["strip", "m", "f"];
  let drag = null;

  const onHandle = (t) => t === grip || !!t.closest(".sbody > .handle");

  function stripHeight() {
    const previous = sheet.dataset.pos;
    sheet.dataset.pos = "strip";
    const height = strip.getBoundingClientRect().height;
    sheet.dataset.pos = previous;
    return height;
  }

  function startDrag(e) {
    if (desk.matches || e.button > 0 || drag) return;
    if (e.target.closest("button") && !onHandle(e.target)) return;
    // From a button it takes more than a finger's tap slop to be a drag.
    const slop = e.target.closest("button") ? 16 : 6;
    drag = { id: e.pointerId, y: e.clientY, from: sheet.getBoundingClientRect().height,
      pos: pos(), stripHeight: stripHeight(), slop, moved: false };
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
      // From the strip the sheet opens as it is pulled.
      if (pos() === "strip") open();
    }
    const max = sheet.parentElement.clientHeight;
    sheet.style.height = `${Math.max(drag.stripHeight, Math.min(max, drag.from - dy))}px`;
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
    // Measure with intrinsic sizing and full-only rows hidden, never the dragged box.
    const previous = sheet.dataset.pos;
    sheet.dataset.pos = "m";
    const middle = sheet.getBoundingClientRect().height;
    sheet.dataset.pos = previous;
    const heights = { strip: d.stripHeight, m: middle, f: stage };
    let best = "strip";
    let distance = Infinity;
    for (const p of POS) {
      const dd = Math.abs(heights[p] - now);
      if (dd < distance) { distance = dd; best = p; }
    }
    snap(best);
  }
  sheet.addEventListener("pointerup", endDrag);
  // A drag that began on a button of the title line is not a tap on it.
  sheet.addEventListener("click", (e) => {
    if (dragged()) { e.stopPropagation(); e.preventDefault(); }
  }, true);
  // A tap on the grip or the title line (not on its buttons).
  sheet.addEventListener("click", (e) => {
    if (desk.matches || !onHandle(e.target) || e.target.closest("button")) return;
    tap(e.target === grip);
  });
  sheet.addEventListener("pointercancel", endDrag);

  return { dragged };
};
