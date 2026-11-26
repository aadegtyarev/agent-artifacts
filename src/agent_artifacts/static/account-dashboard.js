// Enhance the account's existing Share and Delete forms. Without top-layer
// support they stay in their rows; the menu adds no second submission path.
(() => {
  const page = document.querySelector(".account-workspace");
  const menu = document.getElementById("account-row-menu");
  if (!page || !menu || typeof menu.showPopover !== "function" || typeof menu.showModal !== "function") return;

  const wide = matchMedia("(min-width: 900px)");
  const title = document.getElementById("account-row-menu-title");
  const body = document.getElementById("account-row-menu-body");
  const visible = () => menu.open || menu.matches(":popover-open");
  let active = null;

  function release() {
    if (!active) return;
    const { trigger, row, actions } = active;
    active = null;
    trigger.setAttribute("aria-expanded", "false");
    row.append(actions);
  }

  function close(restoreFocus = true) {
    if (!active) return;
    const trigger = active.trigger;
    // Clear the menu's state before native close events can fire.
    release();
    if (menu.open) menu.close();
    else if (menu.matches(":popover-open")) menu.hidePopover();
    if (restoreFocus) trigger.focus({ preventScroll: true });
  }

  function place() {
    if (!active || !menu.matches(":popover-open")) return;
    const anchor = active.trigger.getBoundingClientRect();
    const { width, height } = menu.getBoundingClientRect();
    const gap = 6;
    const vw = document.documentElement.clientWidth;
    const vh = document.documentElement.clientHeight;
    if (anchor.bottom <= 0 || anchor.top >= vh) { close(false); return; }
    const top = anchor.bottom + gap + height <= vh ? anchor.bottom + gap : anchor.top - height - gap;
    menu.style.left = `${Math.max(gap, Math.min(anchor.right - width, vw - width - gap))}px`;
    menu.style.top = `${Math.max(gap, top)}px`;
  }

  for (const trigger of page.querySelectorAll("[data-account-menu]")) {
    trigger.hidden = false;
    trigger.addEventListener("click", () => {
      if (active?.trigger === trigger && visible()) { close(); return; }
      close(false);
      const row = trigger.closest(".account-row");
      const actions = row.querySelector(":scope > .account-row-actions");
      active = { trigger, row, actions };
      title.textContent = trigger.dataset.rowTitle;
      body.append(actions);
      trigger.setAttribute("aria-expanded", "true");
      if (wide.matches) {
        menu.setAttribute("popover", "auto");
        menu.showPopover({ source: trigger });
        place();
      } else {
        menu.removeAttribute("popover");
        menu.style.removeProperty("left");
        menu.style.removeProperty("top");
        menu.showModal();
      }
      if (active) actions.querySelector("button").focus({ preventScroll: true });
    });
  }
  page.classList.add("account-menus");

  // A desktop popover can close through native light dismissal.
  menu.addEventListener("beforetoggle", (e) => { if (e.newState === "closed") release(); });
  menu.addEventListener("close", () => { if (!visible()) release(); });
  menu.addEventListener("cancel", (e) => { e.preventDefault(); close(); });
  menu.addEventListener("keydown", (e) => {
    if (e.key === "Escape") { e.preventDefault(); close(); }
  });
  menu.addEventListener("click", (e) => {
    if (!active) return;
    const share = e.target.closest("[data-share-link]");
    if (share) {
      e.stopPropagation();
      const from = active.trigger;
      const url = share.dataset.shareLink;
      close();
      document.dispatchEvent(new CustomEvent("aa:share-link", { detail: { url, from } }));
      return;
    }
    if (e.target.closest("[data-account-menu-close]")) { close(); return; }
    if (menu.open && e.target === menu) {
      const r = menu.getBoundingClientRect();
      if (e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom) close();
    }
  });
  wide.addEventListener("change", () => close());
  window.addEventListener("resize", place);
  window.addEventListener("scroll", place, { passive: true });
})();
