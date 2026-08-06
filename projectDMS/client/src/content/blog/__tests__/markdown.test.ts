import { describe, expect, it } from "vitest";

import {
  blocksToText,
  parseInline,
  parseMarkdown,
  resolveHref,
  slugifyHeading,
  type MarkdownBlock,
} from "../markdown";
import { estimateReadingMinutes } from "../reading-time";

function blockOfType<T extends MarkdownBlock["type"]>(
  blocks: MarkdownBlock[],
  type: T,
): Extract<MarkdownBlock, { type: T }> {
  const found = blocks.find((block) => block.type === type);
  if (!found) throw new Error(`no ${type} block found`);
  return found as Extract<MarkdownBlock, { type: T }>;
}

describe("parseMarkdown block structure", () => {
  it("parses headings and demotes h1 to h2 so the page keeps a single h1", () => {
    const blocks = parseMarkdown("# Title\n\n## Section\n\n### Sub\n\n#### Deep");
    expect(blocks.map((b) => b.type === "heading" && b.level)).toEqual([2, 2, 3, 4]);
  });

  it("gives headings stable slug ids", () => {
    const blocks = parseMarkdown("## A claim is a chain, not a narrative");
    expect(blockOfType(blocks, "heading").id).toBe(
      "a-claim-is-a-chain-not-a-narrative",
    );
  });

  it("joins soft-wrapped lines into one paragraph", () => {
    const blocks = parseMarkdown("First line\nsecond line.\n\nNew paragraph.");
    const paragraphs = blocks.filter((b) => b.type === "paragraph");
    expect(paragraphs).toHaveLength(2);
    expect(blocksToText([paragraphs[0]])).toBe("First line second line.");
  });

  it("parses unordered and ordered lists", () => {
    const blocks = parseMarkdown("- one;\n- two.\n\n1. first\n2. second");
    const lists = blocks.filter(
      (b): b is Extract<MarkdownBlock, { type: "list" }> => b.type === "list",
    );
    expect(lists).toHaveLength(2);
    expect(lists[0].ordered).toBe(false);
    expect(lists[0].items).toHaveLength(2);
    expect(lists[1].ordered).toBe(true);
    expect(blocksToText([lists[1]])).toBe("first second");
  });

  it("parses a table into head and rows", () => {
    const table = blockOfType(
      parseMarkdown(
        "| Control need | Spreadsheet |\n|---|---|\n| Version history | Depends on process |\n| Deadline control | Manual follow-up |",
      ),
      "table",
    );
    expect(table.head).toHaveLength(2);
    expect(table.rows).toHaveLength(2);
    expect(blocksToText([table])).toContain("Deadline control");
  });

  it("treats a pipe line without a delimiter row as a paragraph, not a table", () => {
    const blocks = parseMarkdown("| not actually a table");
    expect(blocks.map((b) => b.type)).toEqual(["paragraph"]);
  });

  it("parses blockquotes into nested blocks", () => {
    const quote = blockOfType(parseMarkdown("> What sequence affected the works?"), "blockquote");
    expect(quote.blocks.map((b) => b.type)).toEqual(["paragraph"]);
    expect(blocksToText([quote])).toBe("What sequence affected the works?");
  });

  it("parses thematic breaks", () => {
    expect(parseMarkdown("a\n\n---\n\nb").map((b) => b.type)).toEqual([
      "paragraph",
      "thematicBreak",
      "paragraph",
    ]);
  });

  it("terminates on unusual input instead of looping", () => {
    expect(() => parseMarkdown("|\n>\n-\n#\n\n\n   \n")).not.toThrow();
  });
});

describe("parseInline", () => {
  it("parses bold, italic and inline code", () => {
    expect(parseInline("**bold** and *italic* and `code`").map((n) => n.type)).toEqual([
      "strong",
      "text",
      "emphasis",
      "text",
      "code",
    ]);
  });

  it("keeps markdown characters inside code spans literal", () => {
    const nodes = parseInline("Files named `Register_Final_v7_Updated.xlsx` are a smell");
    expect(nodes[1]).toEqual({
      type: "code",
      value: "Register_Final_v7_Updated.xlsx",
    });
  });

  it("does not treat underscores as emphasis", () => {
    expect(parseInline("EXC_B_140 and SH_210").map((n) => n.type)).toEqual(["text"]);
  });

  it("parses external links and marks them external", () => {
    const [link] = parseInline("[SCL Protocol](https://www.scl.org.uk/protocol.pdf)");
    expect(link).toMatchObject({
      type: "link",
      href: "https://www.scl.org.uk/protocol.pdf",
      external: true,
    });
  });

  it("keeps fragment URLs intact for in-page anchors", () => {
    const [link] = parseInline("[ISO 19650](https://www.iso.org/obp/ui/#iso:std:iso:19650)");
    expect(link).toMatchObject({ type: "link", external: true });
  });

  it("marks root-relative links internal", () => {
    const [link] = parseInline("[Blog](/blog)");
    expect(link).toMatchObject({ type: "link", href: "/blog", external: false });
  });
});

describe("link target safety", () => {
  it.each([
    "javascript:alert(1)",
    "JavaScript:alert(1)",
    "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
    "vbscript:msgbox(1)",
    "file:///etc/passwd",
    "not a url",
  ])("rejects %s", (href) => {
    expect(resolveHref(href)).toBeNull();
  });

  it.each(["https://example.com/a", "http://example.com/a", "/blog", "#section"])(
    "accepts %s",
    (href) => {
      expect(resolveHref(href)).not.toBeNull();
    },
  );

  it("renders an unsafe link as plain text, never as a link node", () => {
    const nodes = parseInline("[click me](javascript:alert(document.cookie))");
    expect(nodes.some((node) => node.type === "link")).toBe(false);
    expect(nodes.map((n) => (n.type === "text" ? n.value : "")).join("")).toContain(
      "click me",
    );
  });

  it("never produces a node carrying raw markup from content", () => {
    const blocks = parseMarkdown(
      "<script>alert(1)</script>\n\n<img src=x onerror=alert(1)>",
    );
    // Angle brackets survive as literal text, which React escapes on render.
    expect(blocksToText(blocks)).toContain("<script>alert(1)</script>");
    expect(blocks.every((block) => block.type === "paragraph")).toBe(true);
  });
});

describe("slugifyHeading", () => {
  it("produces URL-safe fragments", () => {
    expect(slugifyHeading("Step 7: Link events causally—but not causation")).toBe(
      "step-7-link-events-causally-but-not-causation",
    );
  });
});

describe("estimateReadingMinutes", () => {
  it("never returns less than one minute", () => {
    expect(estimateReadingMinutes(parseMarkdown("Short."))).toBe(1);
  });

  it("scales with word count at 200 words per minute", () => {
    const words = Array.from({ length: 450 }, () => "word").join(" ");
    expect(estimateReadingMinutes(parseMarkdown(words))).toBe(3);
  });
});
