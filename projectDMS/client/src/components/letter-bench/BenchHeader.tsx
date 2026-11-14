import React, { useState } from "react";
import { ArrowLeft, CalendarClock } from "lucide-react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { cn } from "@/lib/utils";
import type { BenchResponse, DraftRole } from "@/types/letterBench";
import { deadlineText, deadlineTone } from "./benchLogic";

const TONE_CLASS = {
  overdue: "border-rose-700 bg-rose-600 text-white",
  urgent: "border-rose-600 bg-rose-50 text-rose-900 dark:bg-rose-950 dark:text-rose-100",
  soon: "border-amber-600 bg-amber-50 text-amber-950 dark:bg-amber-950 dark:text-amber-100",
  calm: "border-border bg-muted text-foreground",
  unknown: "border-dashed border-muted-foreground/50 bg-background text-muted-foreground",
} as const;

interface BenchHeaderProps {
  bench: BenchResponse;
  onRole: (role: DraftRole) => void;
  onDeadline: (date: string, basis?: string) => void;
}

export const BenchHeader: React.FC<BenchHeaderProps> = ({ bench, onRole, onDeadline }) => {
  const { board } = bench;
  const [date, setDate] = useState(board.deadline.due_date ?? "");
  const [basis, setBasis] = useState(board.deadline.source === "manual" ? (board.deadline.basis ?? "") : "");
  const [open, setOpen] = useState(false);
  const tone = deadlineTone(board.deadline.days_remaining);
  const reference = board.incoming_meta.letter_no || board.trigger?.label;

  return (
    <header className="flex flex-wrap items-center gap-x-4 gap-y-2 border-b px-4 py-3">
      <Button asChild variant="ghost" size="sm" className="h-9 px-2">
        <Link to="/letters" aria-label="Back to letters">
          <ArrowLeft className="h-4 w-4" aria-hidden="true" />
        </Link>
      </Button>
      <div className="min-w-0 flex-1">
        <h1 className="truncate text-lg font-semibold leading-tight text-foreground">
          {bench.letter_title || "Letter Bench"}
        </h1>
        <p className="truncate text-xs text-muted-foreground">
          {[
            reference && `Ref ${reference}`,
            board.incoming_meta.letter_date && `dated ${board.incoming_meta.letter_date}`,
            board.incoming_meta.sender && `from ${board.incoming_meta.sender}`,
          ]
            .filter(Boolean)
            .join(" · ") || "Incoming letter"}
        </p>
      </div>

      <div className="flex items-center gap-2">
        <label htmlFor="bench-role" className="text-xs font-medium text-muted-foreground">
          Drafting as
        </label>
        <Select value={board.role} onValueChange={(value) => onRole(value as DraftRole)} disabled={bench.read_only}>
          <SelectTrigger id="bench-role" className="h-9 w-36">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="contractor">Contractor</SelectItem>
            <SelectItem value="engineer">Engineer</SelectItem>
            <SelectItem value="employer">Employer</SelectItem>
          </SelectContent>
        </Select>
      </div>

      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <button
            type="button"
            data-testid="deadline-strip"
            disabled={bench.read_only}
            className={cn(
              "inline-flex min-h-9 items-center gap-2 rounded-md border px-3 text-sm font-semibold",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2",
              TONE_CLASS[tone]
            )}
          >
            <CalendarClock className="h-4 w-4" aria-hidden="true" />
            <span>{deadlineText(board.deadline.days_remaining)}</span>
            {board.deadline.basis && board.deadline.due_date && (
              <span className="hidden font-normal opacity-90 xl:inline">({board.deadline.basis})</span>
            )}
            {tone === "unknown" && !bench.read_only && <span className="font-normal underline">Set it</span>}
          </button>
        </PopoverTrigger>
        <PopoverContent align="end" className="w-80 space-y-3">
          <div>
            <p className="text-sm font-semibold">Reply date</p>
            <p className="text-xs text-muted-foreground">
              {board.deadline.source === "extracted" && board.deadline.raw
                ? `The letter says “${board.deadline.raw}”. Change it if the Contract sets a different date.`
                : "No reply date was found in the letter. Enter the date the Contract requires."}
            </p>
          </div>
          <div className="space-y-1">
            <label htmlFor="bench-deadline-date" className="text-xs font-medium">
              Reply due on
            </label>
            <Input
              id="bench-deadline-date"
              type="date"
              value={date}
              onChange={(event) => setDate(event.target.value)}
            />
          </div>
          <div className="space-y-1">
            <label htmlFor="bench-deadline-basis" className="text-xs font-medium">
              Set by (clause or instruction)
            </label>
            <Input
              id="bench-deadline-basis"
              value={basis}
              maxLength={200}
              placeholder="e.g. Sub-Clause 3.7"
              onChange={(event) => setBasis(event.target.value)}
            />
          </div>
          <Button
            size="sm"
            disabled={!date}
            onClick={() => {
              onDeadline(date, basis.trim() || undefined);
              setOpen(false);
            }}
          >
            Save reply date
          </Button>
        </PopoverContent>
      </Popover>
    </header>
  );
};

export default BenchHeader;
