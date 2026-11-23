// Links between files: a link in a page to another file or project of the
// service opens it in the viewer's own tab instead of inside the frame. No
// DOM, so node can load it for the tests in tests/js.
// One copy for three places: the viewer loads it as a script before
// comments-pins.js, pages.py puts it in the block with pins.js inside a page
// and before the Markdown renderer, which leaves such links in the same tab.
const pageLinks = (() => {
  const PAGE = /^\/(a|p)\/[A-Za-z0-9_-]{22}$/;
  const FRAGMENT = /^[A-Za-z0-9_.:-]{0,64}$/;

  // In the page: the service page a link's href points at, as {path,
  // fragment}, or null. Resolved against the page's own address, not its
  // <base href>. In the viewer's frame that address is the service's own
  // /a/{id}/raw (the sandbox makes the origin opaque, not the URL), so
  // "/a/…" and the service's full URLs count. A query string, /raw,
  // /download and other sites do not.
  function target(href, base) {
    let url;
    try { url = new URL(href, base); } catch { return null; }
    const origin = new URL(base).origin;
    if (origin === "null" || url.origin !== origin || url.search || !PAGE.test(url.pathname)) return null;
    return { path: url.pathname, fragment: url.hash.slice(1) };
  }

  // In the viewer: the address to open for what the frame sent, built on
  // the viewer's own origin from the two parts only if both are as expected;
  // else null.
  function destination(path, fragment, origin) {
    if (typeof path !== "string" || !PAGE.test(path)) return null;
    if (typeof fragment !== "string" || !FRAGMENT.test(fragment)) return null;
    return origin + path + (fragment ? `#${fragment}` : "");
  }

  // The viewer's own #fragment, to pass on to the content; "" if it has
  // none or one that is not as expected.
  const fragmentOf = (hash) => {
    const f = typeof hash === "string" ? hash.replace(/^#/, "") : "";
    return FRAGMENT.test(f) ? f : "";
  };

  // Frozen: inside a page, the page's own scripts may get hold of it.
  return Object.freeze({ target, destination, fragmentOf });
})();

if (typeof module === "object" && module) module.exports = pageLinks;
