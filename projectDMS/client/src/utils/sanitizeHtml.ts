const ALLOWED_TAGS = new Set([
  "a",
  "b",
  "blockquote",
  "br",
  "code",
  "div",
  "em",
  "h1",
  "h2",
  "h3",
  "h4",
  "h5",
  "h6",
  "hr",
  "i",
  "li",
  "ol",
  "p",
  "pre",
  "s",
  "span",
  "strong",
  "sub",
  "sup",
  "table",
  "tbody",
  "td",
  "th",
  "thead",
  "tr",
  "u",
  "ul",
]);

const ALLOWED_ATTRIBUTES = new Set(["class", "href", "rel", "target", "title"]);
const DROPPED_CONTENT_TAGS = new Set(["script", "style", "iframe", "object"]);
const SAFE_URL_PATTERN = /^(https?:|mailto:|tel:|\/|#)/i;
const SAFE_CLASS_TOKEN_PATTERN = /^[a-z0-9:_/-]+$/i;

const sanitizeClassName = (value: string) =>
  value
    .split(/\s+/)
    .filter((token) => SAFE_CLASS_TOKEN_PATTERN.test(token))
    .join(" ");

const sanitizeElement = (source: Element, doc: Document): Node => {
  const tagName = source.tagName.toLowerCase();

  if (DROPPED_CONTENT_TAGS.has(tagName)) {
    return doc.createTextNode("");
  }

  if (!ALLOWED_TAGS.has(tagName)) {
    return doc.createTextNode(source.textContent || "");
  }

  const target = doc.createElement(tagName);
  for (const attr of Array.from(source.attributes)) {
    const name = attr.name.toLowerCase();
    if (!ALLOWED_ATTRIBUTES.has(name) || name.startsWith("on")) {
      continue;
    }

    if (name === "class") {
      const className = sanitizeClassName(attr.value);
      if (className) target.setAttribute("class", className);
      continue;
    }

    if (name === "href") {
      const href = attr.value.trim();
      if (SAFE_URL_PATTERN.test(href)) target.setAttribute("href", href);
      continue;
    }

    if (name === "target") {
      if (attr.value === "_blank") {
        target.setAttribute("target", "_blank");
        target.setAttribute("rel", "noopener noreferrer");
      }
      continue;
    }

    target.setAttribute(name, attr.value);
  }

  for (const child of Array.from(source.childNodes)) {
    target.appendChild(sanitizeNode(child, doc));
  }

  return target;
};

const sanitizeNode = (node: Node, doc: Document): Node => {
  if (node.nodeType === Node.TEXT_NODE) {
    return doc.createTextNode(node.textContent || "");
  }
  if (node.nodeType === Node.ELEMENT_NODE) {
    return sanitizeElement(node as Element, doc);
  }
  return doc.createTextNode("");
};

export const sanitizeHtml = (html: string): string => {
  if (typeof window === "undefined" || typeof DOMParser === "undefined") {
    return "";
  }

  const parser = new DOMParser();
  const sourceDoc = parser.parseFromString(`<div>${html}</div>`, "text/html");
  const targetDoc = document.implementation.createHTMLDocument("");
  const container = targetDoc.createElement("div");
  const sourceRoot = sourceDoc.body.firstElementChild;

  if (!sourceRoot) return "";

  for (const child of Array.from(sourceRoot.childNodes)) {
    container.appendChild(sanitizeNode(child, targetDoc));
  }

  return container.innerHTML;
};
