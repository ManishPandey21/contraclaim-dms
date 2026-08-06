/**
 * Markdown-subset parser for blog content.
 *
 * Deliberately narrow: it covers exactly the constructs used by the editorial
 * content (headings, paragraphs, lists, tables, blockquotes, rules, and inline
 * bold/italic/code/links) and produces a typed block tree.
 *
 * The tree is rendered as React elements by `MarkdownContent`, so the blog has
 * no `dangerouslySetInnerHTML` sink anywhere. Content cannot inject markup or
 * script — not because a sanitiser strips it, but because nothing in this
 * pipeline ever converts a string into HTML.
 */

export type InlineNode =
  | { type: "text"; value: string }
  | { type: "strong"; children: InlineNode[] }
  | { type: "emphasis"; children: InlineNode[] }
  | { type: "code"; value: string }
  | { type: "link"; href: string; external: boolean; children: InlineNode[] };

export type HeadingLevel = 2 | 3 | 4;

export type MarkdownBlock =
  | { type: "heading"; level: HeadingLevel; id: string; children: InlineNode[] }
  | { type: "paragraph"; children: InlineNode[] }
  | { type: "list"; ordered: boolean; items: InlineNode[][] }
  | { type: "table"; head: InlineNode[][]; rows: InlineNode[][][] }
  | { type: "blockquote"; blocks: MarkdownBlock[] }
  | { type: "thematicBreak" };

const HEADING_RE = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const THEMATIC_BREAK_RE = /^ {0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/;
const UNORDERED_ITEM_RE = /^ {0,3}[-*+]\s+(.*)$/;
const ORDERED_ITEM_RE = /^ {0,3}\d{1,9}[.)]\s+(.*)$/;
const BLOCKQUOTE_RE = /^ {0,3}>\s?(.*)$/;
const TABLE_ROW_RE = /^ {0,3}\|(.*)$/;
const TABLE_DELIMITER_CELL_RE = /^:?-{1,}:?$/;

/**
 * Matches, in priority order: inline code, links, strong, emphasis.
 *
 * Code is matched first so that markdown characters inside a code span stay
 * literal. Underscore emphasis is intentionally unsupported — the content uses
 * asterisks, and supporting `_` would misfire on identifiers such as
 * `Register_Final_v7.xlsx`.
 */
const INLINE_RE =
  /(`[^`]+`)|(\[[^\]]*\]\([^)\s]+\))|(\*\*[\s\S]+?\*\*)|(\*[^*\n]+\*)/g;

const LINK_RE = /^\[([^\]]*)\]\(([^)\s]+)\)$/;

/**
 * Resolve a markdown link target to a safe href.
 *
 * Only fragment, root-relative and http(s) targets are accepted. Everything
 * else — `javascript:`, `data:`, `vbscript:`, malformed input — returns null,
 * and the caller renders the link text as plain text instead.
 */
export function resolveHref(
  raw: string,
): { href: string; external: boolean } | null {
  const value = raw.trim();
  if (!value) return null;
  if (value.startsWith("#") || value.startsWith("/")) {
    return { href: value, external: false };
  }

  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  return { href: url.toString(), external: true };
}

export function slugifyHeading(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 80);
}

function pushText(nodes: InlineNode[], value: string): void {
  if (!value) return;
  const last = nodes[nodes.length - 1];
  if (last && last.type === "text") {
    last.value += value;
    return;
  }
  nodes.push({ type: "text", value });
}

export function parseInline(source: string): InlineNode[] {
  const nodes: InlineNode[] = [];
  if (!source) return nodes;

  // A fresh regex per call keeps `lastIndex` state local, which matters
  // because parseInline recurses into strong/emphasis content.
  const scanner = new RegExp(INLINE_RE.source, "g");
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = scanner.exec(source)) !== null) {
    pushText(nodes, source.slice(cursor, match.index));
    cursor = match.index + match[0].length;

    const [token, codeToken, linkToken, strongToken, emphasisToken] = match;

    if (codeToken) {
      nodes.push({ type: "code", value: codeToken.slice(1, -1) });
    } else if (linkToken) {
      const link = LINK_RE.exec(linkToken);
      const resolved = link ? resolveHref(link[2]) : null;
      if (link && resolved) {
        nodes.push({
          type: "link",
          href: resolved.href,
          external: resolved.external,
          children: parseInline(link[1]),
        });
      } else {
        // Unsafe or unparseable target: keep the visible text, drop the link.
        pushText(nodes, link ? link[1] : token);
      }
    } else if (strongToken) {
      nodes.push({ type: "strong", children: parseInline(strongToken.slice(2, -2)) });
    } else if (emphasisToken) {
      nodes.push({
        type: "emphasis",
        children: parseInline(emphasisToken.slice(1, -1)),
      });
    }
  }

  pushText(nodes, source.slice(cursor));
  return nodes;
}

function splitTableRow(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

function isTableDelimiter(line: string | undefined): boolean {
  if (!line || !TABLE_ROW_RE.test(line)) return false;
  const cells = splitTableRow(line);
  return cells.length > 0 && cells.every((cell) => TABLE_DELIMITER_CELL_RE.test(cell));
}

function startsNewBlock(line: string): boolean {
  return (
    !line.trim() ||
    HEADING_RE.test(line) ||
    THEMATIC_BREAK_RE.test(line) ||
    UNORDERED_ITEM_RE.test(line) ||
    ORDERED_ITEM_RE.test(line) ||
    BLOCKQUOTE_RE.test(line) ||
    TABLE_ROW_RE.test(line)
  );
}

function clampHeadingLevel(hashes: number): HeadingLevel {
  // The page renders the article title as the single h1, so body headings
  // start at h2 regardless of how the source was written.
  if (hashes <= 2) return 2;
  if (hashes === 3) return 3;
  return 4;
}

export function parseMarkdown(source: string): MarkdownBlock[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: MarkdownBlock[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];

    if (!line.trim()) {
      index += 1;
      continue;
    }

    if (THEMATIC_BREAK_RE.test(line)) {
      blocks.push({ type: "thematicBreak" });
      index += 1;
      continue;
    }

    const heading = HEADING_RE.exec(line);
    if (heading) {
      const text = heading[2];
      blocks.push({
        type: "heading",
        level: clampHeadingLevel(heading[1].length),
        id: slugifyHeading(stripInlineMarkup(text)),
        children: parseInline(text),
      });
      index += 1;
      continue;
    }

    if (BLOCKQUOTE_RE.test(line)) {
      const quoted: string[] = [];
      while (index < lines.length && BLOCKQUOTE_RE.test(lines[index])) {
        quoted.push(BLOCKQUOTE_RE.exec(lines[index])![1]);
        index += 1;
      }
      blocks.push({ type: "blockquote", blocks: parseMarkdown(quoted.join("\n")) });
      continue;
    }

    if (TABLE_ROW_RE.test(line) && isTableDelimiter(lines[index + 1])) {
      const head = splitTableRow(line).map(parseInline);
      index += 2;
      const rows: InlineNode[][][] = [];
      while (index < lines.length && TABLE_ROW_RE.test(lines[index])) {
        rows.push(splitTableRow(lines[index]).map(parseInline));
        index += 1;
      }
      blocks.push({ type: "table", head, rows });
      continue;
    }

    const orderedStart = ORDERED_ITEM_RE.exec(line);
    const unorderedStart = UNORDERED_ITEM_RE.exec(line);
    if (orderedStart || unorderedStart) {
      const ordered = Boolean(orderedStart);
      const pattern = ordered ? ORDERED_ITEM_RE : UNORDERED_ITEM_RE;
      const items: InlineNode[][] = [];
      while (index < lines.length) {
        const item = pattern.exec(lines[index]);
        if (!item) break;
        items.push(parseInline(item[1]));
        index += 1;
      }
      blocks.push({ type: "list", ordered, items });
      continue;
    }

    // Consume the current line unconditionally so the loop always advances,
    // then absorb following lines until the next block starts. Soft line
    // breaks inside a paragraph are joined with a space.
    const paragraph: string[] = [line.trim()];
    index += 1;
    while (index < lines.length && !startsNewBlock(lines[index])) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push({ type: "paragraph", children: parseInline(paragraph.join(" ")) });
  }

  return blocks;
}

/** Strip inline markers from a raw markdown string (used for heading ids). */
function stripInlineMarkup(text: string): string {
  return inlineToText(parseInline(text));
}

export function inlineToText(nodes: InlineNode[]): string {
  return nodes
    .map((node) => {
      switch (node.type) {
        case "text":
          return node.value;
        case "code":
          return node.value;
        case "strong":
        case "emphasis":
        case "link":
          return inlineToText(node.children);
        default:
          return "";
      }
    })
    .join("");
}

/** Flatten a block tree to plain text — used for reading time and excerpts. */
export function blocksToText(blocks: MarkdownBlock[]): string {
  return blocks
    .map((block) => {
      switch (block.type) {
        case "heading":
        case "paragraph":
          return inlineToText(block.children);
        case "list":
          return block.items.map(inlineToText).join(" ");
        case "table":
          return [block.head, ...block.rows]
            .map((row) => row.map(inlineToText).join(" "))
            .join(" ");
        case "blockquote":
          return blocksToText(block.blocks);
        default:
          return "";
      }
    })
    .filter(Boolean)
    .join(" ");
}

/** Headings available for an in-page contents list. */
export function collectHeadings(
  blocks: MarkdownBlock[],
): { id: string; level: HeadingLevel; text: string }[] {
  return blocks
    .filter(
      (block): block is Extract<MarkdownBlock, { type: "heading" }> =>
        block.type === "heading",
    )
    .map((block) => ({
      id: block.id,
      level: block.level,
      text: inlineToText(block.children),
    }));
}
