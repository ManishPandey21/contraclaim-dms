import React, { useCallback, useEffect, useMemo, useRef } from "react";
import { Plus, Scissors, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { useLetterBenchStore } from "@/stores/useLetterBenchStore";
import type { Assertion, TextSpan } from "@/types/letterBench";
import { VERDICT_LABELS } from "@/types/letterBench";
import {
  assertionAtOffset,
  buildSegments,
  offsetWithin,
  scrollWithinPane,
  unlocatedAssertions,
} from "./benchLogic";
import { UNDECIDED_MARK, VERDICT_TONE } from "./verdictStyles";

const MIN_SELECTION = 10;

interface IncomingLetterCanvasProps {
  text: string;
  assertions: Assertion[];
  readOnly: boolean;
  busy: boolean;
  onCreate: (span: TextSpan) => void;
  onSplit: (assertionId: string, splitAt: number) => void;
}

/**
 * The incoming letter with each assertion highlighted and numbered in place.
 * Selecting plain text offers "Add as assertion"; placing the selection inside
 * a highlight offers "Split here". Both are also reachable from the keyboard:
 * the highlights are buttons and the offer is a normal toolbar.
 */
export const IncomingLetterCanvas: React.FC<IncomingLetterCanvasProps> = ({
  text,
  assertions,
  readOnly,
  busy,
  onCreate,
  onSplit,
}) => {
  const rootRef = useRef<HTMLDivElement>(null);
  const selectedId = useLetterBenchStore((state) => state.selectedAssertionId);
  const activeId = useLetterBenchStore((state) => state.activeAssertionId);
  const pending = useLetterBenchStore((state) => state.pendingSelection);
  const selectAssertion = useLetterBenchStore((state) => state.selectAssertion);
  const setActive = useLetterBenchStore((state) => state.setActiveAssertion);
  const setPending = useLetterBenchStore((state) => state.setPendingSelection);

  const segments = useMemo(() => buildSegments(text, assertions), [text, assertions]);
  const unlocated = useMemo(() => unlocatedAssertions(text, assertions), [text, assertions]);

  const readSelection = useCallback(() => {
    if (readOnly) return;
    const root = rootRef.current;
    const selection = window.getSelection();
    if (!root || !selection || selection.rangeCount === 0 || selection.isCollapsed) {
      return;
    }
    const range = selection.getRangeAt(0);
    const start = offsetWithin(root, range.startContainer, range.startOffset);
    const end = offsetWithin(root, range.endContainer, range.endOffset);
    if (start === null || end === null || end <= start) return;
    const inside = assertionAtOffset(assertions, start);
    if (inside) {
      setPending({
        start,
        end,
        text: text.slice(start, end),
        insideAssertionId: inside.assertion.assertion_id,
        splitAt: inside.relative,
      });
      return;
    }
    if (end - start < MIN_SELECTION) return;
    const overlaps = assertions.some((a) => a.span && start < a.span.end && a.span.start < end);
    if (overlaps) return;
    setPending({ start, end, text: text.slice(start, end) });
  }, [assertions, readOnly, setPending, text]);

  useEffect(() => {
    if (!selectedId) return;
    scrollWithinPane(rootRef.current?.querySelector<HTMLElement>(`[data-assertion="${selectedId}"]`));
  }, [selectedId]);

  const clearPending = () => {
    setPending(null);
    window.getSelection()?.removeAllRanges();
  };

  if (!text.trim()) {
    return (
      <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
        {busy ? "Reading the letter…" : "No text could be read from this document."}
      </div>
    );
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      {pending && !readOnly && (
        <div
          role="toolbar"
          aria-label="Selection actions"
          className="flex flex-wrap items-center gap-2 border-b bg-primary/5 px-4 py-2 text-sm"
        >
          <span className="min-w-0 flex-1 truncate text-muted-foreground">
            “{pending.text.slice(0, 90)}
            {pending.text.length > 90 ? "…" : ""}”
          </span>
          {pending.insideAssertionId ? (
            <Button
              size="sm"
              onClick={() => {
                onSplit(pending.insideAssertionId as string, pending.splitAt as number);
                clearPending();
              }}
            >
              <Scissors className="mr-1.5 h-4 w-4" aria-hidden="true" />
              Split {pending.insideAssertionId} here
            </Button>
          ) : (
            <Button
              size="sm"
              onClick={() => {
                onCreate({ start: pending.start, end: pending.end });
                clearPending();
              }}
            >
              <Plus className="mr-1.5 h-4 w-4" aria-hidden="true" />
              Add as assertion
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={clearPending} aria-label="Dismiss selection">
            <X className="h-4 w-4" aria-hidden="true" />
          </Button>
        </div>
      )}
      <div
        ref={rootRef}
        data-testid="letter-canvas"
        data-bench-scroll=""
        onMouseUp={readSelection}
        onKeyUp={(event) => {
          if (event.shiftKey || event.key === "Shift") readSelection();
        }}
        className="relative min-h-0 flex-1 overflow-y-auto whitespace-pre-wrap break-words px-5 py-4 font-serif text-[15px] leading-7 text-foreground"
      >
        {segments.map((segment) => {
          const assertion = segment.assertion;
          if (!assertion) return <span key={segment.key}>{segment.text}</span>;
          const id = assertion.assertion_id;
          const lit = id === selectedId || id === activeId;
          return (
            <mark
              key={segment.key}
              data-assertion={id}
              role="button"
              tabIndex={0}
              aria-pressed={id === selectedId}
              aria-label={`Assertion ${id}, ${
                assertion.verdict ? VERDICT_LABELS[assertion.verdict] : "undecided"
              }: ${assertion.quoted_text.slice(0, 120)}`}
              onClick={() => {
                if (window.getSelection()?.isCollapsed ?? true) selectAssertion(id);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  selectAssertion(id);
                }
              }}
              onMouseEnter={() => setActive(id)}
              onMouseLeave={() => setActive(null)}
              onFocus={() => setActive(id)}
              onBlur={() => setActive(null)}
              className={cn(
                "cursor-pointer rounded-sm px-0.5 text-inherit underline decoration-2 underline-offset-4 transition-colors",
                "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                assertion.verdict ? VERDICT_TONE[assertion.verdict].mark : UNDECIDED_MARK,
                lit && "ring-2 ring-primary"
              )}
            >
              <span
                data-bench-skip=""
                aria-hidden="true"
                className="mr-1 inline-flex select-none items-center rounded bg-foreground px-1 align-baseline font-sans text-[10px] font-bold leading-4 text-background"
              >
                {id}
              </span>
              {segment.text}
            </mark>
          );
        })}
      </div>
      {unlocated.length > 0 && (
        <div className="border-t bg-muted/40 px-5 py-3 text-xs">
          <p className="font-medium text-muted-foreground">Points not placed on the text</p>
          <ul className="mt-1 space-y-1">
            {unlocated.map((assertion) => (
              <li key={assertion.assertion_id}>
                <button
                  type="button"
                  onClick={() => selectAssertion(assertion.assertion_id)}
                  className="text-left text-foreground underline-offset-2 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="font-semibold">{assertion.assertion_id}</span>{" "}
                  {assertion.quoted_text.slice(0, 140)}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
};

export default IncomingLetterCanvas;
