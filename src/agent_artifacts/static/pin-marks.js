// What a pin holds: the author's letter, or the agent's star; and the one
// badge at its corner, the dot.
// One copy for both places pins are drawn: the viewer loads it as a script
// before comments.js, and pages.py puts it in a block with pins.js inside a
// page, so `pinMarks` stays out of the page's globals there.
const pinMarks = (() => {
  const MARK = {
    star: "M6 .6C6.45 4 8 5.55 11.4 6 8 6.45 6.45 8 6 11.4 5.55 8 4 6.45.6 6 4 5.55 5.55 4 6 .6z",
    check: "M4.7 8.8 5.9 7.6 7.8 9.5 10.8 6.5 12 7.7 7.8 11.9Z",
  };
  // The only badge: "new", a plain dot.
  const BADGES = { new: "" };
  // The label that stands for the agent's star inside a pin.
  const STAR = "*";
  const APPROVED = "*✓";
  const isStar = (label) => label === STAR || label === APPROVED;
  // The dot's colour by its name in Settings' Dot; any other name is red.
  // The values of tokens.md (--dot-red, --dot-green, --dot-yellow):
  // inside a page no tokens reach.
  const DOTS = { red: "#de4f44", green: "#23a136", yellow: "#c9a100" };
  const dotColour = (name) => DOTS[typeof name === "string"
    && Object.prototype.hasOwnProperty.call(DOTS, name) ? name : "red"];

  function mark(name) {
    const ns = "http://www.w3.org/2000/svg";
    const svg = document.createElementNS(ns, "svg");
    svg.setAttribute("viewBox", "0 0 12 12");
    svg.setAttribute("aria-hidden", "true");
    const path = (d) => {
      const node = document.createElementNS(ns, "path");
      node.setAttribute("d", MARK[d]);
      node.style.cssText = "fill:currentColor;stroke:none";
      return node;
    };
    if (name === "approved") {
      const star = path("star");
      star.setAttribute("transform", "translate(0 .1) scale(.68)");
      svg.append(star, path("check"));
    } else svg.append(path(name));
    return svg;
  }

  function badge(name) {
    const span = document.createElement("span");
    span.className = `bdg ${name}`;
    if (BADGES[name]) span.append(mark(BADGES[name]));
    return span;
  }

  // Keep approval inside the mark so the unread dot can still use the corner.
  const inside = (label) => isStar(label)
    ? mark(label === APPROVED ? "approved" : "star") : document.createTextNode(label);

  const nameOf = (label) => label === APPROVED ? "Approved agent proposal"
    : isStar(label) ? "The agent's comment" : label ? `Comment by ${label}` : "The spot of the new draft";

  return { BADGES, isStar, mark, badge, inside, nameOf, dotColour };
})();
