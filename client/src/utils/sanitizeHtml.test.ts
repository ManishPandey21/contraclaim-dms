import { describe, expect, it } from "vitest";
import { sanitizeHtml } from "./sanitizeHtml";

describe("sanitizeHtml", () => {
  it("removes scripts, event handlers, and unsafe hrefs", () => {
    const result = sanitizeHtml(
      '<p onclick="alert(1)">Hello <script>alert(2)</script><a href="javascript:alert(3)" target="_blank">link</a></p>'
    );

    expect(result).toBe('<p>Hello <a target="_blank" rel="noopener noreferrer">link</a></p>');
  });

  it("preserves safe formatting and class names used by template previews", () => {
    const result = sanitizeHtml(
      '<h2 class="font-bold text-lg unsafe[token]">Title</h2><hr class="my-4"/><p><strong>Body</strong></p>'
    );

    expect(result).toBe(
      '<h2 class="font-bold text-lg">Title</h2><hr class="my-4"><p><strong>Body</strong></p>'
    );
  });
});
