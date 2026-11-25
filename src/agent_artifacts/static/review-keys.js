// The viewer and the sandboxed file share the same key and typing checks.
const reviewKeys = (() => {
  const typing = 'input, textarea, select, [contenteditable]:not([contenteditable="false"]), [role="textbox"], [role="searchbox"], [role="combobox"], [role="dialog"]';
  function command(e) {
    if (e.defaultPrevented || e.isComposing || e.keyCode === 229 || e.repeat
        || e.altKey || e.ctrlKey || e.metaKey || e.getModifierState?.("AltGraph")) return null;
    const key = typeof e.key === "string" ? e.key.toLowerCase() : "";
    if (key !== "c" && key !== "\u0441" && (key !== "f" || e.shiftKey)) return null;
    // In an open shadow tree the event's target can be retargeted to the
    // host; the path still includes the editing control.
    const path = e.composedPath?.() || [e.target];
    if (path.some(node => node?.isContentEditable || node?.closest?.(typing))) return null;
    if (key === "f") return "fullscreen";
    return e.shiftKey ? "toggle-comments" : "start-comment";
  }
  return { command };
})();
if (typeof window === "undefined" && typeof module !== "undefined") module.exports = reviewKeys;
