import { Fragment, type ReactNode } from "react";
import { Link } from "react-router-dom";

import type { InlineNode, MarkdownBlock } from "@/content/blog/markdown";

/**
 * Renders a parsed markdown block tree as React elements.
 *
 * There is deliberately no `dangerouslySetInnerHTML` here or anywhere else in
 * the blog. Content becomes elements, never HTML strings, so article text
 * cannot introduce markup or script no matter what it contains. Link targets
 * were already scheme-checked by the parser; anything unsafe arrived as plain
 * text and never reaches an `href`.
 */

function renderInline(nodes: InlineNode[], keyPrefix: string): ReactNode {
  return nodes.map((node, index) => {
    const key = `${keyPrefix}-${index}`;

    switch (node.type) {
      case "text":
        return <Fragment key={key}>{node.value}</Fragment>;
      case "strong":
        return (
          <strong key={key} className="font-semibold text-ink">
            {renderInline(node.children, key)}
          </strong>
        );
      case "emphasis":
        return <em key={key}>{renderInline(node.children, key)}</em>;
      case "code":
        return (
          <code
            key={key}
            className="rounded bg-brand-soft px-1.5 py-0.5 font-mono text-[0.9em] text-ink"
          >
            {node.value}
          </code>
        );
      case "link":
        if (!node.external) {
          return (
            <Link
              key={key}
              to={node.href}
              className="font-medium text-brand underline underline-offset-4 hover:text-[#1157a8]"
            >
              {renderInline(node.children, key)}
            </Link>
          );
        }
        return (
          <a
            key={key}
            href={node.href}
            target="_blank"
            rel="noopener noreferrer"
            className="font-medium text-brand underline underline-offset-4 hover:text-[#1157a8]"
          >
            {renderInline(node.children, key)}
          </a>
        );
      default:
        return null;
    }
  });
}

function renderBlock(block: MarkdownBlock, key: string): ReactNode {
  switch (block.type) {
    case "heading": {
      const Tag = `h${block.level}` as "h2" | "h3" | "h4";
      const sizes = {
        2: "mt-12 font-serif text-2xl font-semibold tracking-[-0.01em] text-ink md:text-3xl",
        3: "mt-9 font-serif text-xl font-semibold text-ink md:text-2xl",
        4: "mt-7 font-serif text-lg font-semibold text-ink",
      } as const;
      return (
        <Tag key={key} id={block.id} className={`scroll-mt-28 ${sizes[block.level]}`}>
          {renderInline(block.children, key)}
        </Tag>
      );
    }

    case "paragraph":
      return (
        <p key={key} className="mt-5 text-[1.0625rem] leading-8 text-ink/75">
          {renderInline(block.children, key)}
        </p>
      );

    case "list": {
      const ListTag = block.ordered ? "ol" : "ul";
      return (
        <ListTag
          key={key}
          className={`mt-5 space-y-2.5 pl-6 text-[1.0625rem] leading-8 text-ink/75 ${
            block.ordered ? "list-decimal" : "list-disc"
          } marker:text-brand`}
        >
          {block.items.map((item, index) => (
            <li key={`${key}-i${index}`} className="pl-1.5">
              {renderInline(item, `${key}-i${index}`)}
            </li>
          ))}
        </ListTag>
      );
    }

    case "table":
      return (
        // The table scrolls inside its own container so a wide comparison
        // never forces the page body to scroll sideways on a phone.
        <div
          key={key}
          className="mt-7 overflow-x-auto rounded-xl border border-ink/10"
        >
          <table className="w-full min-w-[34rem] border-collapse text-left text-sm">
            <thead className="bg-brand-soft/60">
              <tr>
                {block.head.map((cell, index) => (
                  <th
                    key={`${key}-h${index}`}
                    scope="col"
                    className="border-b border-ink/10 px-4 py-3 font-semibold text-ink"
                  >
                    {renderInline(cell, `${key}-h${index}`)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={`${key}-r${rowIndex}`} className="odd:bg-paper">
                  {row.map((cell, cellIndex) => (
                    <td
                      key={`${key}-r${rowIndex}c${cellIndex}`}
                      className="border-b border-ink/[0.07] px-4 py-3 align-top leading-6 text-ink/75"
                    >
                      {renderInline(cell, `${key}-r${rowIndex}c${cellIndex}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );

    case "blockquote":
      return (
        <blockquote
          key={key}
          className="mt-7 border-l-4 border-brand bg-brand-tint px-5 py-1 [&>p]:text-ink/80"
        >
          {block.blocks.map((inner, index) => renderBlock(inner, `${key}-q${index}`))}
        </blockquote>
      );

    case "thematicBreak":
      return <hr key={key} className="mt-10 border-ink/10" />;

    default:
      return null;
  }
}

export function MarkdownContent({
  blocks,
  className,
}: {
  blocks: MarkdownBlock[];
  className?: string;
}) {
  return (
    <div className={className}>
      {blocks.map((block, index) => renderBlock(block, `b${index}`))}
    </div>
  );
}

export default MarkdownContent;
