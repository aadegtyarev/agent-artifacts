// The badge at a pin's corner: the agent's state or the person's decision.
// One copy for both places pins are drawn: the viewer loads it as a script
// before comments.js, and pages.py puts it in a block with pins.js inside a
// page, so `pinMarks` stays out of the page's globals there.
const pinMarks = (() => {
  const MARK = {
    star: "M6 .6C6.45 4 8 5.55 11.4 6 8 6.45 6.45 8 6 11.4 5.55 8 4 6.45.6 6 4 5.55 5.55 4 6 .6z",
    check: "M2.4 6.3l2.4 2.4L9.6 3.6",
    ask: "M4 4.3a2 2 0 1 1 2.9 1.8c-.6.3-.9.7-.9 1.3v.4M6 9.6v.2",
    reply: "M4.8 2.8L2 5.6l2.8 2.8M2.3 5.6h5a2.6 2.6 0 0 1 2.6 2.6v1",
    cross: "M3 3l6 6M9 3L3 9",
  };
  // Each badge's mark; "new" is a plain dot.
  const BADGES = { wait: "star", work: "star", fin: "star", wrote: "ask", ask: "ask", ok: "check", re: "reply", no: "cross", new: "" };

  function mark(name) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 12 12");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", MARK[name]);
    // Inline styles: the viewer's icon rules (fill, stroke) must not reach it.
    if (name === "star") path.style.cssText = "fill:#fff;stroke:none";
    else path.style.cssText = "fill:none;stroke:#fff;stroke-width:2;stroke-linecap:round;stroke-linejoin:round";
    svg.append(path);
    return svg;
  }

  function badge(name) {
    const span = document.createElement("span");
    span.className = `bdg ${name}`;
    if (BADGES[name]) span.append(mark(BADGES[name]));
    return span;
  }

  return { BADGES, mark, badge };
})();
