// The theme chosen in this browser ("light" or "dark"; none follows the
// system). Loaded in <head>, before the page is drawn, so it never flashes.
(() => {
  try {
    const t = localStorage.getItem("aa-theme");
    if (t === "light" || t === "dark") document.documentElement.dataset.theme = t;
  } catch {
    // No storage: the system's theme.
  }
})();
