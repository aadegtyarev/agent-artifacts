// A project's file list: the project page's, and the one that drops down
// under the header of a file in the project (or from the floating menu's
// Project files in full screen). A row's dot, "needs you", comes from
// /p/{id}/summary, asked with this browser's author key (the one comments.js
// keeps): a comment waiting for this person's decision, or their own review
// not finished yet (live drafts). Only refresh a visible list; keep its
// last result when a refresh fails.
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
  function authorHeaders() {
    try {
      const key = localStorage.getItem("agent-artifacts:author-key");
      if (/^[A-Za-z0-9_-]{22}$/.test(key || "")) return { "X-Author-Key": key };
    } catch { /* no storage: asked as a stranger */ }
    return {};
  }

  const some = (n) => Number.isInteger(n) && n > 0;
  function dot() {
    const el = document.createElement("span");
    el.className = "dot";
    el.setAttribute("role", "img");
    el.setAttribute("aria-label", "Needs you");
    el.title = "Needs you";
    return el;
  }

  const visible = () => !document.hidden && (!drop || !drop.hidden);
  let dotsTimer = null;
  let loadingDots = false;
  let refreshDots = false;

  async function loadDots() {
    clearTimeout(dotsTimer);
    if (!visible()) return;
    if (loadingDots) {
      refreshDots = true;
      return;
    }
    loadingDots = true;
    try {
      const r = await fetch(`/p/${list.dataset.project}/summary`, {
        headers: authorHeaders(), cache: "no-store",
      });
      const summary = r.ok ? await r.json() : null;
      if (!visible() || !summary || typeof summary !== "object" || Array.isArray(summary)) return;
      for (const row of list.querySelectorAll(".pf[data-id]")) {
        const s = summary[row.dataset.id];
        const shown = row.querySelector(".dot");
        if (s && (some(s.waiting_you) || some(s.drafts))) {
          if (!shown) row.append(dot());
        } else if (shown) shown.remove();
      }
    } catch { /* keep the last summary and retry */ }
    finally {
      loadingDots = false;
      if (visible()) dotsTimer = setTimeout(loadDots, refreshDots ? 0 : 8000);
      refreshDots = false;
    }
  }
  document.addEventListener("visibilitychange", loadDots);
  window.addEventListener("pageshow", (e) => { if (e.persisted) loadDots(); });

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
    place();
    drop.hidden = false;
    loadDots();
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
    clearTimeout(dotsTimer);
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
