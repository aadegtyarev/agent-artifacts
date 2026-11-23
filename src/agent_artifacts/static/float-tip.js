// The floating ≡'s double tap and the tip shown on entering full screen
// (viewer.js): when a second tap counts as a double tap, how many times the
// tip has been shown in this browser, and where it goes beside the button.
// No DOM, so node can load it for the tests in tests/js.
const floatTip = (() => {
  const DOUBLE_MS = 300; // the second press comes within this of the first tap's release
  const DOUBLE_PX = 20; // and this close to it
  const KEY = "aa-full-tip"; // {"shown": N} or {"hidden": true}
  const MOST = 3; // shown on this many entries at most, unless hidden before

  // `first`: the last tap ({at, x, y}, its release) or null; `press`: the
  // new press, the same shape.
  const isDoubleTap = (first, press) => Boolean(first) && press.at - first.at >= 0
    && press.at - first.at <= DOUBLE_MS && Math.hypot(press.x - first.x, press.y - first.y) <= DOUBLE_PX;

  // What localStorage holds, read leniently: anything unknown is a fresh start.
  function read(raw) {
    let saved = null;
    try { saved = JSON.parse(raw || "null"); } catch { /* fresh */ }
    if (saved && saved.hidden === true) return { shown: 0, hidden: true };
    const shown = saved && Number.isInteger(saved.shown) && saved.shown > 0 ? saved.shown : 0;
    return { shown, hidden: false };
  }
  const due = (state) => !state.hidden && state.shown < MOST;
  const counted = (state) => JSON.stringify({ shown: state.shown + 1 });
  const hidden = () => JSON.stringify({ hidden: true });

  // The tip's top left corner: above the button when it fits, else under
  // it, its edge in line with the button's on the side the button keeps
  // to, and never past the window's edges (`gap` in); its top left corner
  // stays in sight when it is larger than the window.
  function place(button, tip, view, side, gap) {
    let top = button.top - gap - tip.height;
    if (top < gap) top = button.bottom + gap;
    top = Math.max(gap, Math.min(top, view.height - tip.height - gap));
    let left = side === "left" ? button.left : button.right - tip.width;
    left = Math.max(gap, Math.min(left, view.width - tip.width - gap));
    return { left, top };
  }

  return Object.freeze({ KEY, MOST, isDoubleTap, read, due, counted, hidden, place });
})();

if (typeof module === "object" && module) module.exports = floatTip;
