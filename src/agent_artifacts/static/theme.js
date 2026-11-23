// The theme chosen in this browser ("light" or "dark"; none follows the
// system), and the size of the service's own text ("normal", "large",
// "larger"; none is large). Loaded in <head>, before the page is drawn, so
// neither ever flashes.
(() => {
  const root = document.documentElement;
  root.dataset.textSize = "large";
  try {
    const t = localStorage.getItem("aa-theme");
    if (t === "light" || t === "dark") root.dataset.theme = t;
    const s = localStorage.getItem("agent-artifacts:text-size");
    if (s === "normal" || s === "larger") root.dataset.textSize = s;
  } catch {
    // No storage: the system's theme, large text.
  }
})();
