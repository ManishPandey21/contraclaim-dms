/**
 * The one Tiptap surface in this application, pinned across a security upgrade.
 *
 * GHSA-cp6q-959q-f8rh (`@tiptap/core` <= 3.30.3) reports that
 * `mergeAttributes()` treats an own `__proto__` key in an attribute object as a
 * prototype assignment, so attributes an author never declared - including
 * event handlers - can end up inherited by every attribute object the renderer
 * produces. The remediation moved `@tiptap/*` from 3.15.0 to 3.31.2.
 *
 * That advisory is **reachable here**. `RichTextEditor` is rendered by
 * `LetterTemplateEditorPage` with `section.content`, which is HTML fetched from
 * `api.getLetterTemplate(id)` - persisted markup authored by other users of the
 * same tenant. It is server-stored user input, not a literal.
 *
 * Before this file nothing in the suite mounted the editor at all, so a sixteen
 * minor-version jump on a shipped rich-text engine had no test standing behind
 * it. These cases cover the two things the upgrade could plausibly break -
 * parsing supplied HTML, and the extension wiring behind the toolbar - plus the
 * property the advisory is about: loading hostile content must not put an event
 * handler into the document or onto `Object.prototype`.
 *
 * **The third case did not fail on the vulnerable 3.15.0.** It was run there
 * first and passed, so it is a standing property guard, not a reproduction of
 * the advisory. That result is itself evidence: the exploit needs an attribute
 * object whose *keys* come from untrusted input, which in Tiptap means a custom
 * extension whose `parseHTML` forwards arbitrary keys into `mergeAttributes`.
 * This application defines no extensions of its own and never calls
 * `mergeAttributes`; it composes StarterKit, Underline, TextAlign and
 * Placeholder, all of which declare a fixed attribute set, and unknown
 * attributes on parsed HTML are dropped before an attribute object is built.
 * The vulnerable code shipped in the bundle; the precondition to reach it did
 * not exist. The upgrade removes the code anyway.
 *
 * If a custom Tiptap extension is ever added here, this reasoning expires and
 * the reachability question has to be asked again.
 */

import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";

import RichTextEditor from "../RichTextEditor";

// ProseMirror measures selections through the Range API, which jsdom does not
// implement. Without these it throws before any assertion can run, which would
// make the suite report an environment gap as a Tiptap regression.
beforeAll(() => {
  const range = globalThis.Range?.prototype as
    | (Range & { getClientRects?: unknown; getBoundingClientRect?: unknown })
    | undefined;
  if (range && !range.getClientRects) {
    range.getClientRects = () => ({ length: 0, item: () => null, [Symbol.iterator]: function* () {} });
    range.getBoundingClientRect = () => ({
      x: 0, y: 0, top: 0, left: 0, right: 0, bottom: 0, width: 0, height: 0, toJSON: () => ({}),
    }) as DOMRect;
  }
});

/** Event-handler attributes are the payload the advisory turns executable. */
const HANDLER_ATTRIBUTES = ["onerror", "onload", "onclick", "onmouseover", "onfocus"];

afterEach(() => {
  for (const attribute of HANDLER_ATTRIBUTES) {
    delete (Object.prototype as Record<string, unknown>)[attribute];
  }
});

const editorRoot = () => document.querySelector(".ProseMirror") as HTMLElement | null;

describe("RichTextEditor", () => {
  it("renders the HTML the server supplied for a template section", async () => {
    render(
      <RichTextEditor
        content="<p>The Contractor <strong>shall</strong> notify the Engineer.</p>"
        onChange={vi.fn()}
      />,
    );

    await waitFor(() => expect(editorRoot()).not.toBeNull());
    expect(editorRoot()!.textContent).toContain("The Contractor shall notify the Engineer.");
    expect(editorRoot()!.querySelector("strong")).not.toBeNull();
  });

  it("keeps the toolbar wired to the extensions and reports edits as HTML", async () => {
    const onChange = vi.fn();
    const user = userEvent.setup();

    render(<RichTextEditor content="<p>Original clause.</p>" onChange={onChange} />);
    await waitFor(() => expect(editorRoot()).not.toBeNull());

    await user.click(screen.getByTitle("Bullet List"));

    await waitFor(() => expect(onChange).toHaveBeenCalled());
    const emitted = onChange.mock.calls.at(-1)![0] as string;
    expect(emitted).toContain("<ul>");
    expect(emitted).toContain("Original clause.");
  });

  it("does not turn hostile stored content into executable attributes", async () => {
    // Each shape puts `__proto__` where an attribute object is built from
    // parsed input: as a bare attribute, inside a style-carrying node, and on a
    // link, which is the one node type that keeps an author-controlled URL.
    const hostile = [
      '<p __proto__="{&quot;onerror&quot;:&quot;alert(1)&quot;}">clause one</p>',
      '<p style="text-align: center" __proto__="x" onerror="alert(1)">clause two</p>',
      '<a href="https://example.test" __proto__="y" onload="alert(1)">clause three</a>',
    ].join("");

    render(<RichTextEditor content={hostile} onChange={vi.fn()} />);
    await waitFor(() => expect(editorRoot()).not.toBeNull());

    for (const attribute of HANDLER_ATTRIBUTES) {
      expect(
        (Object.prototype as Record<string, unknown>)[attribute],
        `Object.prototype.${attribute} was assigned while loading editor content`,
      ).toBeUndefined();
      expect(
        editorRoot()!.querySelector(`[${attribute}]`),
        `an ${attribute} attribute reached the rendered document`,
      ).toBeNull();
    }
    expect(editorRoot()!.innerHTML).not.toContain("alert(1)");
  });
});
