// The theme chosen in this browser ("light" or "dark"; none follows the
// system). Loaded in <head>, before the page is drawn, so it never flashes.
// The dark palette is dark.css; its link's media follows the system, and a
// chosen theme makes it always or never apply. The menu's Theme changes
// [data-theme] later; the link follows that too.
(() => {
  const root = document.documentElement;
  const dark = document.getElementById("dark-css");
  const MEDIA = { dark: "all", light: "not all" };
  const apply = () => {
    if (dark) dark.media = MEDIA[root.dataset.theme] || "(prefers-color-scheme: dark)";
  };
  try {
    const t = localStorage.getItem("aa-theme");
    if (t === "light" || t === "dark") root.dataset.theme = t;
  } catch {
    // No storage: the system's theme.
  }
  apply();
  new MutationObserver(apply).observe(root, { attributes: true, attributeFilter: ["data-theme"] });
})();
