// What Settings (menu-dialogs.js) keeps in this browser, applied in <head>
// before the page is drawn, so none of it ever flashes: the theme ("light"
// or "dark"; none follows the system), the size of the service's own text
// ("normal", "large", "larger"; none is large), and the pins: large ones
// (class pins-big), and whose told by shape as well as colour (class
// pins-shaped, the default; "color" turns it off), and the colour of the
// notification dot (data-dot "green" or "yellow"; none is red).
(() => {
  const root = document.documentElement;
  root.dataset.textSize = "large";
  let shapes = true;
  try {
    const t = localStorage.getItem("aa-theme");
    if (t === "light" || t === "dark") root.dataset.theme = t;
    const s = localStorage.getItem("agent-artifacts:text-size");
    if (s === "normal" || s === "larger") root.dataset.textSize = s;
    if (localStorage.getItem("agent-artifacts:pin-size") === "large") root.classList.add("pins-big");
    shapes = localStorage.getItem("agent-artifacts:pin-shapes") !== "color";
    const d = localStorage.getItem("agent-artifacts:dot");
    if (d === "green" || d === "yellow") root.dataset.dot = d;
  } catch {
    // No storage: the system's theme, large text, normal pins with shapes,
    // a red dot.
  }
  root.classList.toggle("pins-shaped", shapes);
})();
