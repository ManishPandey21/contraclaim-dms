import React from "react";
import { Loader2, Lock, PenLine, Send } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { BenchResponse } from "@/types/letterBench";
import { decidedCount } from "./benchLogic";

interface PositionGateBarProps {
  bench: BenchResponse;
  busy: boolean;
  pending: boolean;
  onLock: () => void;
  onCompose: () => void;
  onSubmit: () => void;
}

/** Sticky gate: what still blocks the next step, and the one action that moves the letter on. */
export const PositionGateBar: React.FC<PositionGateBarProps> = ({
  bench,
  busy,
  pending,
  onLock,
  onCompose,
  onSubmit,
}) => {
  const { board, gate } = bench;
  const total = board.assertions.length;
  const decided = decidedCount(board.assertions);
  const hasReply = board.reply_paragraphs.length > 0;
  const composed = board.stage === "composed" && Boolean(board.draft_run_id);

  let message = gate.blockers[0] ?? "";
  if (!message) {
    if (gate.can_lock) message = "Every position is set. Lock them to compose the reply.";
    else if (board.composing) message = "Composing the reply…";
    else if (gate.can_compose) message = hasReply ? "Save the reply as a new draft version." : "Positions locked. Compose the reply.";
    else if (composed) message = "Draft saved. Submit it for review when ready.";
  }

  return (
    <footer className="flex flex-wrap items-center gap-3 border-t bg-background px-4 py-3">
      <div className="flex items-center gap-3">
        <div
          role="progressbar"
          aria-label="Positions set"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={decided}
          className="h-2 w-28 overflow-hidden rounded-full bg-muted"
        >
          <div
            className="h-full rounded-full bg-primary transition-[width] motion-reduce:transition-none"
            style={{ width: total ? `${(decided / total) * 100}%` : "0%" }}
          />
        </div>
        <span className="text-sm font-semibold tabular-nums" data-testid="positions-count">
          {decided} of {total} set
        </span>
      </div>
      <p role="status" aria-live="polite" className="min-w-0 flex-1 text-sm text-muted-foreground">
        {message}
      </p>
      {!bench.read_only && (
        <div className="flex items-center gap-2">
          {composed ? (
            <Button onClick={onSubmit} disabled={pending || busy}>
              <Send className="mr-2 h-4 w-4" aria-hidden="true" />
              Submit for review
            </Button>
          ) : board.stage === "locked" || board.composing ? (
            <Button onClick={onCompose} disabled={!gate.can_compose || pending || busy}>
              {board.composing ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden="true" />
              ) : (
                <PenLine className="mr-2 h-4 w-4" aria-hidden="true" />
              )}
              {hasReply ? "Save reply as draft" : "Compose reply"}
            </Button>
          ) : (
            <Button onClick={onLock} disabled={!gate.can_lock || pending || busy}>
              <Lock className="mr-2 h-4 w-4" aria-hidden="true" />
              Lock positions
            </Button>
          )}
        </div>
      )}
    </footer>
  );
};

export default PositionGateBar;
