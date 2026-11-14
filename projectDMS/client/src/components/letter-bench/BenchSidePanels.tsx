import React, { useEffect, useState } from "react";
import { AlertTriangle, EyeOff, HelpCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { BoardQuestion } from "@/types/letterBench";

/** The skill's internal strategy note and residual risks. Shown to the team, never exported. */
export const InternalNotePanel: React.FC<{ note?: string | null }> = ({ note }) => {
  if (!note) return null;
  return (
    <aside aria-label="Internal note" className="mx-4 mb-4 rounded-md border border-dashed bg-muted/40 p-3">
      <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
        Internal note · not part of the letter
      </p>
      <p className="mt-1.5 whitespace-pre-wrap text-sm text-foreground">{note}</p>
    </aside>
  );
};

interface QuestionCardProps {
  item: BoardQuestion;
  readOnly: boolean;
  onAnswer: (text: string) => void;
}

/** A question that must be answered before the reply can be composed. */
export const QuestionCard: React.FC<QuestionCardProps> = ({ item, readOnly, onAnswer }) => {
  const id = `question-${item.question.question_id}`;
  const [text, setText] = useState(item.answer ?? "");
  useEffect(() => setText(item.answer ?? ""), [item.answer]);
  const answered = Boolean((item.answer ?? "").trim());
  return (
    <article
      aria-labelledby={`${id}-label`}
      className={`rounded-lg border bg-card p-4 ${answered ? "border-border" : "border-l-4 border-l-sky-600"}`}
    >
      <p id={`${id}-label`} className="flex gap-2 text-sm font-medium text-foreground">
        <HelpCircle className="mt-0.5 h-4 w-4 shrink-0 text-sky-700 dark:text-sky-300" aria-hidden="true" />
        <span>
          <span className="mr-1.5 rounded bg-sky-100 px-1 text-[11px] font-semibold uppercase text-sky-900 dark:bg-sky-900 dark:text-sky-100">
            Need information
          </span>
          {item.question.question}
        </span>
      </p>
      {item.question.why && <p className="ml-6 mt-1 text-xs text-muted-foreground">{item.question.why}</p>}
      <div className="ml-6 mt-2 space-y-2">
        <label htmlFor={id} className="sr-only">
          Answer
        </label>
        <Textarea
          id={id}
          value={text}
          rows={2}
          maxLength={4000}
          disabled={readOnly}
          placeholder="Your answer"
          onChange={(event) => setText(event.target.value)}
        />
        {!readOnly && (
          <Button
            size="sm"
            variant={answered ? "outline" : "default"}
            disabled={!text.trim() || text.trim() === (item.answer ?? "")}
            onClick={() => onAnswer(text.trim())}
          >
            {answered ? "Update answer" : "Save answer"}
          </Button>
        )}
      </div>
    </article>
  );
};

export const BenchErrorBanner: React.FC<{ message?: string | null }> = ({ message }) => {
  if (!message) return null;
  return (
    <div role="alert" className="flex gap-2 border-b border-amber-600 bg-amber-50 px-4 py-2 text-sm text-amber-950 dark:bg-amber-950 dark:text-amber-100">
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <span>{message}</span>
    </div>
  );
};
