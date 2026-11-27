// Marked parses published messages. Only our own elements and text nodes reach
// the viewer DOM: neither raw HTML nor an image can run code or load resources.
const commentMarkdown = (() => {
  function linkUrl(href) {
    if (!/^https?:\/\//i.test(href) || /[\x00-\x20\x7f\\]/.test(href)) return null;
    try {
      const url = new URL(href);
      return url.username || url.password ? null : url.href;
    } catch {
      return null;
    }
  }

  function append(parent, tokens, inLink = false) {
    const doc = parent.ownerDocument;
    const add = (tag, children = [], inside = inLink) => {
      const node = doc.createElement(tag);
      append(node, children, inside);
      parent.append(node);
      return node;
    };
    for (const token of tokens) {
      switch (token.type) {
        case "space": case "def": break;
        case "paragraph": add("p", token.tokens); break;
        case "heading": append(add("p"), [{ ...token, type: "strong" }], inLink); break;
        case "strong": case "em": case "del":
          add(token.type, token.tokens);
          break;
        case "br": case "hr": add(token.type); break;
        case "codespan": add("code").textContent = token.text; break;
        case "code": {
          const pre = add("pre");
          const code = doc.createElement("code");
          code.textContent = token.text;
          pre.append(code);
          break;
        }
        case "blockquote": add("blockquote", token.tokens); break;
        case "list": {
          const list = add(token.ordered ? "ol" : "ul", token.items);
          if (token.ordered) list.start = token.start;
          break;
        }
        case "list_item": add("li", token.tokens); break;
        case "link": {
          const href = inLink ? null : linkUrl(token.href);
          if (!href) { parent.append(token.raw); break; }
          const link = add("a", token.tokens, true);
          link.href = href;
          link.target = "_blank";
          link.rel = "noopener noreferrer";
          break;
        }
        case "text":
          if (token.tokens) append(parent, token.tokens, inLink);
          else parent.append(token.text);
          break;
        case "escape": parent.append(token.text); break;
        default: parent.append(token.raw); // HTML, images and unsupported markup stay text.
      }
    }
  }

  function render(node, text) {
    try {
      const content = node.ownerDocument.createDocumentFragment();
      append(content, marked.lexer(text));
      node.replaceChildren(content);
    } catch {
      // Missing parser or a message it cannot handle: still show the source.
      node.textContent = text;
    }
  }

  return { render };
})();
