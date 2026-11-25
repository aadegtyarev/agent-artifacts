// The two dialogs of the ≡ menu (viewer.js sends aa:dialog), on the viewer
// and the project page. Share: the page's link to copy, the file to
// download. Settings: the name comments are signed with, the theme, the
// text size, the pins' size, whether whose they are shows by shape too and
// the colour of the notification dot.
// Each setting applies at once and is kept in this browser; theme.js
// applies them before a page is drawn. Without storage they last as long as
// the page.
(() => {
  const $ = (id) => document.getElementById(id);
  const root = document.documentElement;
  const dialogs = { share: $("share-dialog"), settings: $("settings-dialog") };
  if (!dialogs.share || !dialogs.settings) return;

  const read = (key) => {
    try { return localStorage.getItem(key); } catch { return null; }
  };
  // null removes: the default is kept as no value.
  const write = (key, value) => {
    try {
      if (value === null) localStorage.removeItem(key);
      else localStorage.setItem(key, value);
    } catch { /* no storage: it lasts while the page is open */ }
  };

  // --- Open and close -----------------------------------------------------------
  // One at a time. The focus moves into the dialog and, on closing, back to
  // the button the menu was opened from.
  let shown = null;
  let back = null;

  function open(name, from) {
    if (shown) close();
    shown = dialogs[name];
    back = from || null;
    if (name === "settings") fillSettings();
    else $("share-note").textContent = "";
    shown.hidden = false;
    shown.querySelector(".modal").focus({ preventScroll: true });
  }

  function close() {
    if (!shown) return;
    if (shown === dialogs.settings) saveName();
    shown.hidden = true;
    shown = null;
    if (back && back.isConnected && back.getClientRects().length) back.focus({ preventScroll: true });
    back = null;
  }

  document.addEventListener("aa:dialog", (e) => {
    const { name, from } = e.detail || {};
    if (Object.prototype.hasOwnProperty.call(dialogs, name)) open(name, from);
  });
  // A tap on the scrim closes; a drag that only ends there (selecting the
  // link or the name) does not.
  for (const scrim of Object.values(dialogs)) {
    let pressed = null;
    scrim.addEventListener("pointerdown", (e) => { pressed = e.target; });
    scrim.addEventListener("click", (e) => {
      const onScrim = e.target === scrim && pressed === scrim;
      pressed = null;
      if (onScrim || e.target.closest("[data-close]")) close();
    });
  }
  $("settings-form").addEventListener("submit", (e) => {
    e.preventDefault();
    close();
  });

  // Escape closes before anything else on the page sees it; Tab stays inside.
  window.addEventListener("keydown", (e) => {
    if (!shown) return;
    if (e.key === "Escape") {
      e.preventDefault();
      e.stopImmediatePropagation();
      close();
    } else if (e.key === "Tab") {
      const all = [...shown.querySelectorAll("a[href],button,input")]
        .filter((el) => el.getClientRects().length && !(el.type === "radio" && !el.checked));
      if (!all.length) return;
      const first = all[0];
      const last = all[all.length - 1];
      const at = document.activeElement;
      if (e.shiftKey && (at === first || !shown.contains(at) || at === shown.querySelector(".modal"))) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && (at === last || !shown.contains(at))) {
        e.preventDefault();
        first.focus();
      }
    }
  }, true);

  // --- Share ----------------------------------------------------------------------
  // The toast sits under the dialog's scrim, so the dialog says it itself.
  const link = $("share-link");
  const note = $("share-note");
  link.addEventListener("focus", () => link.select());
  $("share-copy").addEventListener("click", () => {
    const copied = navigator.clipboard
      ? navigator.clipboard.writeText(link.value)
      : Promise.reject(new Error("no clipboard"));
    copied.then(
      () => { note.textContent = "Link copied."; },
      () => {
        note.textContent = "Could not copy: select the link above and copy it.";
        link.focus();
      },
    );
  });
  // The download goes on by itself; the dialog is done.
  $("share-download")?.addEventListener("click", () => close());

  // --- Settings -------------------------------------------------------------------
  const form = $("settings-form");
  const nameInput = $("settings-name");
  const NAME_KEY = "agent-artifacts:name";
  let nameShown = "";

  const radios = (name) => [...form.querySelectorAll(`input[name="set-${name}"]`)];
  const check = (name, value) => {
    for (const r of radios(name)) r.checked = r.value === value;
  };

  function fillSettings() {
    if (nameInput) {
      nameShown = read(NAME_KEY) || "";
      nameInput.value = nameShown;
    }
    check("theme", root.dataset.theme || "auto");
    check("text", root.dataset.textSize || "large");
    check("pins", root.classList.contains("pins-big") ? "large" : "normal");
    check("shapes", root.classList.contains("pins-shaped") ? "shape" : "color");
    check("dot", root.dataset.dot || "red");
  }

  // The name is kept only when it was changed here, so a person never asked
  // is still asked when comment mode first comes on. Empty is No name, as
  // the name dialog's button. comments.js cleans it as the server wants.
  function saveName() {
    if (!nameInput) return;
    const value = nameInput.value.trim();
    if (value === nameShown.trim()) return;
    write(NAME_KEY, value);
    document.dispatchEvent(new CustomEvent("aa:name", { detail: { name: value } }));
  }

  $("workspace-sign-out")?.addEventListener("click", async () => {
    try {
      const response = await fetch("/auth/logout", { method: "POST" });
      if (!response.ok) throw new Error("Sign out failed");
      location.replace("/auth/signed-out");
    } catch {
      document.dispatchEvent(new CustomEvent("aa:toast", {
        detail: { message: "Could not sign out. Try again.", error: true },
      }));
    }
  });

  const apply = {
    theme(t) {
      if (t === "auto") delete root.dataset.theme;
      else root.dataset.theme = t;
      write("aa-theme", t === "auto" ? null : t);
    },
    text(s) {
      root.dataset.textSize = s;
      write("agent-artifacts:text-size", s === "large" ? null : s);
      // The panel beside it got wider or narrower: the floating button moves.
      document.dispatchEvent(new CustomEvent("aa:sheet"));
    },
    pins(size) {
      root.classList.toggle("pins-big", size === "large");
      write("agent-artifacts:pin-size", size === "large" ? "large" : null);
      document.dispatchEvent(new CustomEvent("aa:look"));
    },
    shapes(v) {
      root.classList.toggle("pins-shaped", v !== "color");
      write("agent-artifacts:pin-shapes", v === "color" ? "color" : null);
      document.dispatchEvent(new CustomEvent("aa:look"));
    },
    dot(v) {
      const colour = v === "green" || v === "yellow" ? v : null;
      if (colour) root.dataset.dot = colour;
      else delete root.dataset.dot;
      write("agent-artifacts:dot", colour);
      // The pins in a page are told it again.
      document.dispatchEvent(new CustomEvent("aa:look"));
    },
  };
  form.addEventListener("change", (e) => {
    const r = e.target;
    if (r.type !== "radio" || !r.checked) return;
    const name = r.name.replace(/^set-/, "");
    if (Object.prototype.hasOwnProperty.call(apply, name)) apply[name](r.value);
  });
})();
