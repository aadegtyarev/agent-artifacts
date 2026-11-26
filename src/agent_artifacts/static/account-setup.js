// Enhance the same-origin forms. They still work with a normal POST when
// this script is unavailable. Keys and codes never go into a URL or storage.
(() => {
  if (typeof fetch !== "function" || typeof AbortController !== "function") return;

  async function post(form, signal) {
    const response = await fetch(form.action, {
      method: "POST", headers: { Accept: "application/json" }, cache: "no-store",
      body: new URLSearchParams(new FormData(form)), signal,
    });
    if (response.redirected) throw new Error("Account changed");
    return { ok: response.ok, data: await response.json() };
  }

  function status(node, message) {
    node.hidden = !message;
    node.textContent = message;
  }

  for (const panel of document.querySelectorAll("[data-account-claim]")) {
    const form = panel.querySelector("[data-claim-check]");
    const input = form.elements.code;
    const confirm = panel.querySelector("[data-claim-confirm]");
    const note = panel.querySelector("[data-claim-status]");
    const retry = panel.querySelector("[data-claim-retry]");
    let validating;
    form.querySelector("[data-claim-continue]").hidden = true;

    function invalidate() {
      validating?.abort();
      validating = null;
      confirm.hidden = true;
      confirm.elements.proof.value = confirm.elements.proof.defaultValue = "";
      confirm.elements.code.value = confirm.elements.code.defaultValue = "";
      retry.hidden = true;
      status(note, "");
    }

    async function check() {
      invalidate();
      const code = input.value.trim();
      if (/^aa_[A-Za-z0-9_-]{43}$/.test(code)) {
        status(note, "Use the setup code, not the agent's key.");
        return;
      }
      if (!/^[A-Za-z0-9_-]{43}$/.test(code)) {
        if (code.length >= 43) status(note, "Check the setup code.");
        return;
      }
      const attempt = new AbortController();
      validating = attempt;
      status(note, "Checking the code…");
      try {
        const { ok, data } = await post(form, attempt.signal);
        if (validating !== attempt || input.value.trim() !== code) return;
        if (!ok) {
          status(note, typeof data.message === "string" ? data.message : "Check the setup code.");
          return;
        }
        if (!/^[a-f0-9]{64}$/.test(data.proof) || typeof data.agent !== "string") throw new Error("Missing preview");
        panel.querySelector("[data-claim-name]").textContent = data.agent;
        confirm.elements.code.value = code;
        confirm.elements.proof.value = data.proof;
        confirm.hidden = false;
        status(note, "Check the agent and account before linking.");
      } catch {
        if (validating !== attempt || input.value.trim() !== code) return;
        retry.hidden = false;
        status(note, "Could not check the code. Try again.");
      } finally {
        if (validating === attempt) validating = null;
      }
    }

    form.addEventListener("submit", (event) => { event.preventDefault(); check(); });
    input.addEventListener("input", (event) => { if (!event.isComposing) check(); });
    input.addEventListener("compositionend", check);
    retry.addEventListener("click", () => { input.focus({ preventScroll: true }); check(); });
    confirm.addEventListener("submit", (event) => {
      if (!confirm.elements.proof.value || input.value.trim() !== confirm.elements.code.value) {
        event.preventDefault();
        check();
      }
    });
    window.addEventListener("pagehide", () => {
      invalidate();
      input.value = input.defaultValue = "";
    });
  }

  const setup = document.getElementById("account-setup");
  if (!setup) return;
  if (location.hash === "#account-agents") document.getElementById("account-agents").focus({ preventScroll: true });
  const choices = [...setup.querySelectorAll("[data-setup-choice]")];
  const branches = [...setup.querySelectorAll("[data-setup-branch]")];
  const keyForm = setup.querySelector("[data-account-issue]");
  const result = setup.querySelector("[data-key-result]");
  const note = setup.querySelector("[data-key-status]");
  const secret = setup.querySelector("#setup");
  let issuing;

  function mode(selected) {
    for (const button of choices) button.setAttribute("aria-pressed", String(button.dataset.setupChoice === selected));
    for (const branch of branches) branch.inert = branch.dataset.setupBranch !== selected;
  }

  setup.classList.add("account-setup-enhanced");
  mode(choices.find((button) => button.getAttribute("aria-pressed") === "true").dataset.setupChoice);
  for (const branch of branches) branch.hidden = false;
  setup.querySelector("[data-account-modes]").hidden = false;
  for (const route of setup.querySelectorAll("[data-setup-route]")) route.hidden = true;
  for (const button of choices) button.addEventListener("click", () => mode(button.dataset.setupChoice));

  keyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (issuing || keyForm.hidden) return;
    const attempt = new AbortController();
    issuing = attempt;
    const submit = keyForm.querySelector('button[type="submit"]');
    submit.disabled = true;
    for (const choice of choices) choice.disabled = true;
    status(note, "Creating the key…");
    try {
      const { ok, data } = await post(keyForm, attempt.signal);
      if (issuing !== attempt) return;
      if (!ok) {
        status(note, typeof data.message === "string" ? data.message : "Could not create the key.");
        return;
      }
      if (typeof data.setup !== "string" || typeof data.name !== "string") throw new Error("Missing setup");
      secret.value = data.setup;
      setup.querySelector("[data-issued-name]").textContent = data.name;
      keyForm.hidden = true;
      result.hidden = false;
      result.querySelector("[data-copy]").focus({ preventScroll: true });
      status(note, "");
    } catch {
      // A lost response does not mean that the key wasn't created. Never
      // change the name or resubmit on behalf of the person.
      if (issuing === attempt) status(note, "Could not read the setup. Check Agent keys before trying again.");
    } finally {
      if (issuing === attempt) {
        issuing = null;
        submit.disabled = false;
        for (const choice of choices) choice.disabled = false;
      }
    }
  });

  function clearKey() {
    issuing?.abort();
    issuing = null;
    secret.value = secret.defaultValue = "";
    if (!result.hidden) status(note, "The key is no longer shown. Return to your account to manage it.");
    result.hidden = true;
    keyForm.hidden = false;
    keyForm.querySelector('button[type="submit"]').disabled = false;
    for (const choice of choices) choice.disabled = false;
  }

  result.querySelector("[data-key-done]").addEventListener("click", (event) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    clearKey();
    if (location.pathname !== "/account") return;
    // Merely following the fragment from /account would keep the old list.
    event.preventDefault();
    history.replaceState(null, "", "/account#account-agents");
    location.reload();
  });
  window.addEventListener("pagehide", clearKey);
})();
