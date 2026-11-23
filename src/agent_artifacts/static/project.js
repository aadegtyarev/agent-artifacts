// A project's file list: the project page's, and the one that drops down
// under the header of a file in the project (or from the floating menu's
// Project files in full screen). A row's dot, "needs you", comes from
// /p/{id}/summary, asked with this browser's author key (the one comments.js
// keeps): a comment waiting for this person's decision, or their own review
// not finished yet (live drafts). Until it answers, and if it fails, the
// rows show none.
(() => {
  const list = document.getElementById("files");
  if (!list) return;
  const drop = document.getElementById("files-drop");

  // --- The toast --------------------------------------------------------------
  // The project page's; a file's viewer has the comments script's.
  if (!drop) {
    const toastBox = document.getElementById("toast");
    const toastText = document.getElementById("toast-text");
    let toastTimer = null;
    document.addEventListener("aa:toast", (e) => {
      const { message, error } = e.detail || {};
      if (typeof message !== "string") return;
      clearTimeout(toastTimer);
      toastText.textContent = message;
      toastBox.classList.toggle("error", Boolean(error));
      toastBox.classList.add("on");
      toastTimer = setTimeout(() => toastBox.classList.remove("on"), error ? 10000 : 2500);
    });
  }

  // --- The dots ---------------------------------------------------------------
  let key = null;
  try {
    key = localStorage.getItem("agent-artifacts:author-key");
  } catch { /* no storage: asked as a stranger */ }
  const headers = /^[A-Za-z0-9_-]{22}$/.test(key || "") ? { "X-Author-Key": key } : {};

  const some = (n) => Number.isInteger(n) && n > 0;
  function dot() {
    const el = document.createElement("span");
    el.className = "dot";
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", "Needs you");
    el.title = "Needs you";
    return el;
  }

  // On the project page at once; under a file's header when first opened,
  // so a file's page does not ask for them on every load.
  let asked = false;
  function loadDots() {
    if (asked) return;
    asked = true;
    fetch(`/p/${list.dataset.project}/summary`, { headers, cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((summary) => {
      if (!summary || typeof summary !== "object") return;
      for (const row of list.querySelectorAll(".pf[data-id]")) {
        const s = summary[row.dataset.id];
        if (s && (some(s.waiting_you) || some(s.drafts))) row.append(dot());
      }
    })
    .catch(() => { /* no dots */ });
  }

  // --- The list under a file's header -----------------------------------------
  // Opened by the project's title in the header, or in full screen by the
  // floating menu's Project files (viewer.js sends aa:files). A tap on the
  // scrim, Escape or the title again closes it; focus goes back to what
  // opened it. Its rows are plain links: the next file opens in this tab.
  if (!drop) {
    loadDots();
    return;
  }
  const button = document.getElementById("files-button");
  const header = document.querySelector("body > header");
  let opener = null;

  // Just under the header; at the top in full screen, where it is hidden.
  function place() {
    const box = header.getBoundingClientRect();
    drop.style.top = `${box.height ? box.bottom : 0}px`;
  }

  function open(from) {
    opener = from;
    loadDots();
    place();
    drop.hidden = false;
    button.setAttribute("aria-expanded", "true");
    const current = list.querySelector('[aria-current="page"]');
    if (current) current.scrollIntoView({ block: "nearest" });
    (current || list).focus({ preventScroll: true });
  }

  function close(restore = true) {
    if (drop.hidden) return;
    // A tap on the scrim takes the focus off the list: it goes back too.
    const inside = drop.contains(document.activeElement) || document.activeElement === document.body;
    drop.hidden = true;
    button.setAttribute("aria-expanded", "false");
    if (restore && inside && opener && opener.getClientRects().length) {
      opener.focus({ preventScroll: true });
    }
    opener = null;
  }

  button.addEventListener("click", () => (drop.hidden ? open(button) : close()));
  document.addEventListener("aa:files", (e) => open(e.detail?.from || button));
  drop.querySelector(".drop-scrim").addEventListener("click", () => close());
  // The header's other buttons (the menu, comment mode) close it first.
  header.addEventListener("click", (e) => {
    if (!button.contains(e.target)) close(false);
  });
  window.addEventListener("keydown", (e) => {
    if (drop.hidden || e.key !== "Escape") return;
    e.preventDefault();
    e.stopImmediatePropagation();
    close();
  }, true);
  drop.addEventListener("focusout", (e) => {
    if (e.relatedTarget && !drop.contains(e.relatedTarget) && e.relatedTarget !== button) close(false);
  });
  window.addEventListener("resize", () => { if (!drop.hidden) place(); });
})();
