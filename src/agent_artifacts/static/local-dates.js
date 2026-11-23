// Keep the server's UTC text if the browser cannot format local dates.
const localDates = (() => {
  function date(instant, now = new Date()) {
    const t = new Date(instant);
    const options = { month: "short", day: "numeric" };
    if (t.getFullYear() !== now.getFullYear()) options.year = "numeric";
    return new Intl.DateTimeFormat("en-US", options).format(t);
  }
  function deadline(instant) {
    const t = new Date(instant);
    const day = new Intl.DateTimeFormat("en-US", {
      month: "short", day: "numeric", year: "numeric",
    }).format(t);
    const offset = -t.getTimezoneOffset();
    const pad = (n) => String(n).padStart(2, "0");
    const zone = offset === 0 ? "UTC" : `UTC${offset > 0 ? "+" : "-"}${pad(Math.floor(Math.abs(offset) / 60))}:${pad(Math.abs(offset) % 60)}`;
    return `${day} at ${pad(t.getHours())}:${pad(t.getMinutes())} ${zone}`;
  }
  function apply(root) {
    if (typeof Intl === "undefined" || !Intl.DateTimeFormat) return;
    root.querySelectorAll("time[data-local-deadline], time[data-local-date]").forEach((el) => {
      try { el.textContent = el.hasAttribute("data-local-deadline") ? deadline(el.dateTime) : date(el.dateTime); }
      catch { /* retain the server's UTC fallback */ }
    });
  }
  return { date, deadline, apply };
})();
if (typeof document !== "undefined") localDates.apply(document);
if (typeof module !== "undefined") module.exports = localDates;
