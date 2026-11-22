// The project page: the comment counters on its rows, and the toast of the
// menu's Copy link (viewer.js). The counters come from /p/{id}/summary, asked
// with this browser's author key (the one comments.js keeps), so comments
// this person already decided or replied to are not counted. Until it
// answers, and if it fails, the rows show none.
(() => {
  const list = document.getElementById("files");
  if (!list) return;

  // --- The toast --------------------------------------------------------------
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

  // --- The counters -----------------------------------------------------------
  let key = null;
  try {
    key = localStorage.getItem("agent-artifacts:author-key");
  } catch { /* no storage: counted as for a stranger */ }
  const headers = /^[A-Za-z0-9_-]{22}$/.test(key || "") ? { "X-Author-Key": key } : {};

  const count = (n) => (Number.isInteger(n) && n > 0 ? n : 0);
  function counters(waiting, withAgent) {
    const box = document.createElement("span");
    box.className = "cnts";
    if (waiting) {
      const pill = document.createElement("span");
      pill.className = "cnt";
      pill.textContent = String(waiting);
      pill.title = `${waiting} waiting for you`;
      box.append(pill);
    }
    if (withAgent) {
      const agent = document.createElement("span");
      agent.className = "cnt ag";
      agent.title = `${withAgent} with the agent`;
      const pin = document.createElement("span");
      pin.className = "pin mini ag line";
      pin.append(pinMarks.mark("star"));
      agent.append(pin, String(withAgent));
      box.append(agent);
    }
    return box;
  }

  fetch(`/p/${list.dataset.project}/summary`, { headers, cache: "no-store" })
    .then((r) => (r.ok ? r.json() : null))
    .then((summary) => {
      if (!summary || typeof summary !== "object") return;
      for (const row of list.querySelectorAll(".pf[data-id]")) {
        const s = summary[row.dataset.id];
        if (!s) continue;
        const waiting = count(s.waiting_you);
        const withAgent = count(s.with_agent);
        if (waiting || withAgent) row.append(counters(waiting, withAgent));
      }
    })
    .catch(() => { /* no counters */ });
})();
