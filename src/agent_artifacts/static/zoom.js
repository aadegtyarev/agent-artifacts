// Zoom of the file alone: a pinch, a double tap or Ctrl + wheel (a
// trackpad's pinch on a computer) over an image or a page scales that
// content, never the viewer's header, sheet or menu, which the viewer page
// keeps the browser from zooming (its viewport meta). Mobile browsers zoom a
// whole page, never a frame on its own, so the content is scaled here:
// - an image: comments-pins.js sizes the <img> and its box scrolls; the pins
//   are placed in % of the image, so they follow;
// - a page: pins.js, inside the frame, scales the page's <body> by a CSS
//   transform from its top left corner and the page scrolls. The pins' layer
//   is outside <body> and placed from the elements' boxes, which include the
//   transform, so pins stay on their spots at their own size, and a tap
//   lands on what is under the finger, as at 1x.
// At 1x nothing is changed and one finger scrolls as before. No messages:
// the page zooms only itself. The math has no DOM, so node can load it for
// the tests in tests/js; pages.py puts this file in the block with pins.js.
const pageZoom = (() => {
  const DOUBLE_MS = 300; // the second tap of a double tap comes within this
  const DOUBLE_PX = 30; // and this close to the first
  const TAP_PX = 10; // a press that moved less than this is a tap
  const TAP_MS = 500; // and one that lifted within this
  const SNAP = 1.05; // let go under this, the content is back at 1x

  const clamp = (v, lo, hi) => Math.min(hi, Math.max(lo, v));

  // Fingers `startGap` apart at `start`, now `gap` apart.
  function pinchScale(start, startGap, gap, max) {
    if (!(startGap > 0) || !(gap > 0)) return start;
    return clamp(start * (gap / startGap), 1, max);
  }

  // How far to scroll so the point of the content under `focal` at the
  // start stays under the fingers (now at `focal` again or moved). `box`
  // is the content's top left corner in the window, before and now.
  function keepPoint(box, scale, focal, nowBox, nowScale, nowFocal) {
    const x = (focal.x - box.left) / scale;
    const y = (focal.y - box.top) / scale;
    return { dx: nowBox.left + x * nowScale - nowFocal.x, dy: nowBox.top + y * nowScale - nowFocal.y };
  }

  // A double tap: at 1x it zooms in to 2x, zoomed it goes back to 1x.
  const toggled = (scale) => (scale > SNAP ? 1 : 2);

  // A wheel turn with Ctrl (a trackpad's pinch sends these): about 10 %
  // a notch; lines and pages count as notches.
  function wheelScale(scale, deltaY, deltaMode, max) {
    const px = deltaMode === 1 ? deltaY * 33 : deltaMode === 2 ? deltaY * 400 : deltaY;
    return clamp(scale * Math.exp(-clamp(px, -100, 100) / 1000), 1, max);
  }

  // One finger moved (dx, dy) past the tap threshold on zoomed content:
  // whether the script moves it rather than the browser's own scroll. Only
  // where the move is mostly along an axis the content does not scroll by
  // itself (`locked`, {x, y}); along the other the browser scrolls as usual.
  const scriptPan = (dx, dy, locked) => (Math.abs(dx) >= Math.abs(dy) ? locked.x : locked.y) === true;

  // Taps as {x, y, at}: whether `tap` is the second of a double tap.
  const isDoubleTap = (first, tap) => Boolean(first) && tap.at - first.at <= DOUBLE_MS
    && Math.hypot(tap.x - first.x, tap.y - first.y) <= DOUBLE_PX;

  // The gestures on `target` (an element or the window):
  //   max:            the largest scale
  //   scale():        the scale now (1 is the content as laid out)
  //   setScale(s):    show the content at `s`
  //   box():          the content's top left corner in the window, now
  //   scrollBy(x, y): scroll what shows the content
  //   locked():       the axes the browser does not scroll the zoomed
  //                   content along ({x, y}); one finger moves it there
  //   doubleTap(e):   whether a double tap (its second touchend) zooms now
  //   own(e):         whether a touch is the page's own to handle
  // A gesture the page's own script already took (its touchstart or wheel
  // cancelled) is left to it.
  function attach(target, {
    max, scale, setScale, box, scrollBy, locked = () => ({ x: false, y: false }),
    doubleTap = () => true, own = () => false,
  }) {
    let pinch = null;
    let press = null; // one finger down: a tap, or a pan when zoomed
    let lastTap = null;
    let multi = false; // two fingers were down since all were lifted

    const mid = (t) => ({ x: (t[0].clientX + t[1].clientX) / 2, y: (t[0].clientY + t[1].clientY) / 2 });
    const gap = (t) => Math.hypot(t[0].clientX - t[1].clientX, t[0].clientY - t[1].clientY);

    // To `to`, keeping the point under `from` where it was, then under `at`.
    function zoomTo(to, from, at = from, start = { box: box(), scale: scale() }) {
      if (to !== scale()) setScale(to);
      // What it took: an image not loaded yet stays at 1x.
      const d = keepPoint(start.box, start.scale, from, box(), scale(), at);
      if (d.dx || d.dy) scrollBy(d.dx, d.dy);
    }

    target.addEventListener("touchstart", (e) => {
      if (e.defaultPrevented || own(e)) {
        pinch = null;
        press = null;
        return;
      }
      if (e.touches.length >= 2) {
        multi = true;
        press = null;
        lastTap = null;
        e.preventDefault();
        const t = e.touches;
        pinch = { gap: gap(t), focal: mid(t), box: box(), scale: scale() };
      } else if (e.touches.length === 1) {
        const t = e.touches[0];
        press = { x: t.clientX, y: t.clientY, lastX: t.clientX, lastY: t.clientY,
          at: performance.now(), moved: false, script: false };
      }
    }, { passive: false });

    target.addEventListener("touchmove", (e) => {
      if (pinch && e.touches.length >= 2) {
        e.preventDefault();
        const t = e.touches;
        zoomTo(pinchScale(pinch.scale, pinch.gap, gap(t), max), pinch.focal, mid(t), pinch);
        return;
      }
      if (!press || e.touches.length !== 1) return;
      const t = e.touches[0];
      // Past the tap threshold it is a move: the script's or the browser's,
      // decided once.
      if (!press.moved && Math.hypot(t.clientX - press.x, t.clientY - press.y) > TAP_PX) {
        press.moved = true;
        press.script = scale() > 1 && e.cancelable
          && scriptPan(t.clientX - press.x, t.clientY - press.y, locked());
      }
      if (press.script && e.cancelable) {
        e.preventDefault();
        scrollBy(press.lastX - t.clientX, press.lastY - t.clientY);
      }
      press.lastX = t.clientX;
      press.lastY = t.clientY;
    }, { passive: false });

    const end = (e) => {
      if (pinch && e.touches.length < 2) {
        pinch = null;
        if (scale() < SNAP && scale() !== 1) {
          const t = e.changedTouches[0];
          zoomTo(1, { x: t.clientX, y: t.clientY });
        }
        // The finger left on zoomed content moves it: the browser does not
        // start a scroll in the middle of a touch it was kept from.
        if (e.touches.length === 1 && scale() > 1) {
          const t = e.touches[0];
          press = { x: t.clientX, y: t.clientY, lastX: t.clientX, lastY: t.clientY,
            at: performance.now(), moved: true, script: true };
        }
      }
      if (e.touches.length) return;
      const wasMulti = multi;
      multi = false;
      const p = press;
      press = null;
      if (wasMulti || !p || p.moved || e.type === "touchcancel" || performance.now() - p.at > TAP_MS) {
        lastTap = null;
        return;
      }
      const tap = { x: p.x, y: p.y, at: performance.now() };
      if (!isDoubleTap(lastTap, tap) || !doubleTap(e)) {
        lastTap = tap;
        return;
      }
      lastTap = null;
      // The second tap's click is not the page's.
      if (e.cancelable) e.preventDefault();
      zoomTo(toggled(scale()), tap);
    };
    // In the capture phase: pins.js stops a touchend in pin mode.
    target.addEventListener("touchend", end, { capture: true, passive: false });
    target.addEventListener("touchcancel", end, { capture: true });

    target.addEventListener("wheel", (e) => {
      if (!e.ctrlKey || e.defaultPrevented) return;
      e.preventDefault();
      zoomTo(wheelScale(scale(), e.deltaY, e.deltaMode, max), { x: e.clientX, y: e.clientY });
    }, { passive: false });
  }

  return Object.freeze({ clamp, pinchScale, keepPoint, toggled, wheelScale, scriptPan, isDoubleTap, attach });
})();

if (typeof module === "object" && module) module.exports = pageZoom;
