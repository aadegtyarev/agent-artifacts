// What a pin holds: the author's letter, or the agent's star; and the one
// badge at its corner, the red dot.
// One copy for both places pins are drawn: the viewer loads it as a script
// before comments.js, and pages.py puts it in a block with pins.js inside a
// page, so `pinMarks` stays out of the page's globals there.
const pinMarks = (() => {
  const MARK = {
    star: "M6 .6C6.45 4 8 5.55 11.4 6 8 6.45 6.45 8 6 11.4 5.55 8 4 6.45.6 6 4 5.55 5.55 4 6 .6z",
  };
  // The only badge: "new", a plain dot.
  const BADGES = { new: "" };
  // The label that stands for the agent's star inside a pin.
  const STAR = "*";

  function mark(name) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 12 12");
    svg.setAttribute("aria-hidden", "true");
    const path = document.createElementNS(ns, "path");
    path.setAttribute("d", MARK[name]);
    // Filled in the colour of the text around it.
    path.style.cssText = "fill:currentColor;stroke:none";
    svg.append(path);
    return svg;
  }

  function badge(name) {
    const span = document.createElement("span");
    span.className = `bdg ${name}`;
    if (BADGES[name]) span.append(mark(BADGES[name]));
    return span;
  }

  // A pin's inside: its letter, or the star.
  const inside = (label) => (label === STAR ? mark("star") : document.createTextNode(label));

  return { BADGES, STAR, mark, badge, inside };
})();
