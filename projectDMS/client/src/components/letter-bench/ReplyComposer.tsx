import React, { useEffect, useRef, useState } from "react";
import { RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useLetterBenchStore } from "@/stores/useLetterBenchStore";
import type { Assertion, ReplyParagraph } from "@/types/letterBench";
import { VERDICT_LABELS } from "@/types/letterBench";
import { paragraphBadge } from "./benchLogic";
import { VERDICT_TONE } from "./verdictStyles";

const PART_LABEL: Record<string, string> = {
  header: "Header",
  opening: "Opening",
  request: "Requested action",
  reservations: "Reservation of rights",
  attachments: "Attachments",
  closing: "Closing",
};

const BADGE = {
  stale: { text: "Out of date", className: "border-amber-600 bg-amber-50 text-amber-950 dark:bg-amber-950 dark:text-amber-100" },
  template: { text: "Template wording", className: "border-sky-600 bg-sky-50 text-sky-900 dark:bg-sky-950 dark:text-sky-100" },
  edited: { text: "Edited by you", className: "border-border bg-muted text-foreground" },
} as const;

interface ParagraphEditorProps {
  paragraph: ReplyParagraph;
  assertion?: Assertion;
  readOnly: boolean;
  onSave: (text: string) => void;
  onRerun: () => void;
}

const ParagraphEditor: React.FC<ParagraphEditorProps> = ({ paragraph, assertion, readOnly, onSave, onRerun }) => {
  const [text, setText] = useState(paragraph.text);
  const ref = useRef<HTMLTextAreaElement>(null);
  const activeId = useLetterBenchStore((state) => state.activeAssertionId);
  const setActive = useLetterBenchStore((state) => state.setActiveAssertion);
  const badge = paragraphBadge(paragraph);
  const label = assertion
    ? `Reply to ${assertion.assertion_id}`
    : (PART_LABEL[paragraph.part] ?? paragraph.part);

  useEffect(() => setText(paragraph.text), [paragraph.text]);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight + 2}px`;
  }, [text]);

  const linked = assertion?.assertion_id ?? null;
  return (
    <li
      data-testid={`reply-paragraph-${paragraph.paragraph_id}`}
      onMouseEnter={() => linked && setActive(linked)}
      onMouseLeave={() => linked && setActive(null)}
      className={cn(
        "rounded-md border p-3 transition-colors",
        paragraph.stale ? "border-amber-600 bg-amber-50/40 dark:bg-amber-950/20" : "border-border bg-card",
        linked && activeId === linked && "ring-2 ring-primary"
      )}
    >
      <div className="mb-1.5 flex flex-wrap items-center gap-2">
        {assertion ? (
          <span
            className={cn(
              "rounded px-1.5 py-0.5 text-[11px] font-bold text-white",
              assertion.verdict ? VERDICT_TONE[assertion.verdict].dot : "bg-slate-600"
            )}
          >
            {assertion.assertion_id}
          </span>
        ) : null}
        <label htmlFor={`paragraph-${paragraph.paragraph_id}`} className="text-xs font-semibold text-muted-foreground">
          {label}
          {assertion?.verdict ? ` · ${VERDICT_LABELS[assertion.verdict]}` : ""}
        </label>
        {badge && (
          <span className={cn("rounded border px-1.5 py-0.5 text-[11px] font-medium", BADGE[badge].className)}>
            {BADGE[badge].text}
          </span>
        )}
        {assertion && !readOnly && (paragraph.stale || paragraph.degraded) && (
          <Button variant="outline" size="sm" className="ml-auto h-7 text-xs" onClick={onRerun}>
            <RefreshCw className="mr-1 h-3 w-3" aria-hidden="true" />
            Re-run
          </Button>
        )}
      </div>
      <textarea
        ref={ref}
        id={`paragraph-${paragraph.paragraph_id}`}
        value={text}
        readOnly={readOnly}
        rows={2}
        onFocus={() => linked && setActive(linked)}
        onChange={(event) => setText(event.target.value)}
        onBlur={() => {
          if (linked) setActive(null);
          if (text !== paragraph.text) onSave(text);
        }}
        className="w-full resize-none overflow-hidden whitespace-pre-wrap rounded bg-transparent font-serif text-[15px] leading-7 text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      />
    </li>
  );
};

interface ReplyComposerProps {
  paragraphs: ReplyParagraph[];
  assertions: Assertion[];
  readOnly: boolean;
  composing: boolean;
  onSave: (paragraphId: string, text: string) => void;
  onRerun: (assertionId: string) => void;
}

/**
 * The reply, one paragraph per assertion, in the letters skill's structure.
 * Hovering or focusing a paragraph lights up the assertion it answers in the
 * letter and in the positions list.
 */
export const ReplyComposer: React.FC<ReplyComposerProps> = ({
  paragraphs,
  assertions,
  readOnly,
  composing,
  onSave,
  onRerun,
}) => {
  const byId = new Map(assertions.map((assertion) => [assertion.assertion_id, assertion]));
  if (!paragraphs.length) {
    return (
      <p className="p-6 text-center text-sm text-muted-foreground">
        {composing
          ? "Writing one paragraph for each position…"
          : "The reply appears here once positions are locked: one paragraph for each assertion, in order."}
      </p>
    );
  }
  return (
    <ol aria-label="Reply paragraphs" className="space-y-2 p-4">
      {paragraphs.map((paragraph) => {
        const assertion = paragraph.assertion_id ? byId.get(paragraph.assertion_id) : undefined;
        return (
          <ParagraphEditor
            key={paragraph.paragraph_id}
            paragraph={paragraph}
            assertion={assertion}
            readOnly={readOnly}
            onSave={(text) => onSave(paragraph.paragraph_id, text)}
            onRerun={() => assertion && onRerun(assertion.assertion_id)}
          />
        );
      })}
    </ol>
  );
};

export default ReplyComposer;
