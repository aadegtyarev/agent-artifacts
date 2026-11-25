// The server keeps the deadline; a reload only gets the remaining time.
// The forms still submit and enforce that deadline without JavaScript.
(() => {
  const stack = document.getElementById("account-deletions");
  if (!stack) return;
  const next = () => stack.querySelector(".toast-box:not([hidden]) .toast-undo");
  for (const box of stack.querySelectorAll("[data-undo-ms]")) {
    function expire() {
      const hadFocus = box.contains(document.activeElement);
      box.hidden = true;
      const action = next();
      if (!action) stack.hidden = true;
      if (hadFocus) (action || document.querySelector(".account-page h1"))?.focus({ preventScroll: true });
    }
    const remaining = Number(box.dataset.undoMs);
    if (Number.isFinite(remaining) && remaining > 0) setTimeout(expire, remaining);
    else expire();
  }
  if (location.hash.startsWith("#account-undo-")) {
    // Use the exact deletion from the redirect, not another device's later one.
    const box = document.getElementById(location.hash.slice(1));
    const focusUndo = () => requestAnimationFrame(() => {
      if (document.activeElement === document.body && box && stack.contains(box) && !box.hidden) {
        box.querySelector(".toast-undo").focus({ preventScroll: true });
      }
    });
    if (document.readyState === "complete") focusUndo();
    else window.addEventListener("load", focusUndo, { once: true });
  }
})();
