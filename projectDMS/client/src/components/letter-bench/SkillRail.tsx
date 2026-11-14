import React, { useState } from "react";
import {
  AlertTriangle,
  Ban,
  Check,
  ChevronLeft,
  ChevronRight,
  CircleDashed,
  Loader2,
  Plus,
  RefreshCw,
  X,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import type { ChainLink, ChainLinkStatus } from "@/types/letterBench";
import { linkStatusLabel, linkTargetLabel } from "./benchLogic";
import { LINK_STATUS_TONE } from "./verdictStyles";

const STATUS_ICON: Record<ChainLinkStatus, React.ReactNode> = {
  pending: <CircleDashed className="h-3.5 w-3.5" aria-hidden="true" />,
  queued: <CircleDashed className="h-3.5 w-3.5" aria-hidden="true" />,
  running: <Loader2 className="h-3.5 w-3.5 animate-spin motion-reduce:animate-none" aria-hidden="true" />,
  done: <Check className="h-3.5 w-3.5 text-emerald-700 dark:text-emerald-300" aria-hidden="true" />,
  stale: <RefreshCw className="h-3.5 w-3.5 text-amber-700 dark:text-amber-300" aria-hidden="true" />,
  failed: <AlertTriangle className="h-3.5 w-3.5 text-destructive" aria-hidden="true" />,
  unavailable: <Ban className="h-3.5 w-3.5" aria-hidden="true" />,
};

interface SkillRailProps {
  chain: ChainLink[];
  readOnly: boolean;
  onOpen: (link: ChainLink) => void;
  onAdd: () => void;
  onRemove: (link: ChainLink) => void;
  onMove: (link: ChainLink, direction: -1 | 1) => void;
  onDrop: (fromId: string, toId: string) => void;
  onRun: (link: ChainLink) => void;
}

/**
 * The skill chain as an ordered list of chips: which skill, which version, on
 * what, and where it stands. Evaluation links can be dragged to reorder; the
 * arrow buttons do the same from the keyboard. Analysis is always first and
 * the reply always last.
 */
export const SkillRail: React.FC<SkillRailProps> = ({
  chain,
  readOnly,
  onOpen,
  onAdd,
  onRemove,
  onMove,
  onDrop,
  onRun,
}) => {
  const [dragging, setDragging] = useState<string | null>(null);
  const [over, setOver] = useState<string | null>(null);
  const evaluations = chain.filter((link) => link.kind === "evaluation");

  return (
    <nav aria-label="Skill chain" className="flex items-center gap-2 overflow-x-auto px-4 py-2">
      <span className="shrink-0 text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
        Skill chain
      </span>
      <ol className="flex items-stretch gap-1.5">
        {chain.map((link, index) => {
          const movable = link.kind === "evaluation" && !readOnly;
          const position = evaluations.findIndex((item) => item.link_id === link.link_id);
          const target = linkTargetLabel(link);
          const canRun =
            !readOnly && link.kind === "evaluation" && ["pending", "stale", "failed"].includes(link.status);
          return (
            <li
              key={link.link_id}
              data-testid={`chain-link-${link.skill_key}`}
              draggable={movable}
              onDragStart={(event) => {
                setDragging(link.link_id);
                event.dataTransfer.effectAllowed = "move";
                event.dataTransfer.setData("text/plain", link.link_id);
              }}
              onDragOver={(event) => {
                if (movable && dragging && dragging !== link.link_id) {
                  event.preventDefault();
                  setOver(link.link_id);
                }
              }}
              onDragLeave={() => setOver((current) => (current === link.link_id ? null : current))}
              onDrop={(event) => {
                event.preventDefault();
                if (dragging && movable) onDrop(dragging, link.link_id);
                setDragging(null);
                setOver(null);
              }}
              onDragEnd={() => {
                setDragging(null);
                setOver(null);
              }}
              className={cn("flex items-center gap-1.5", movable && "cursor-grab active:cursor-grabbing")}
            >
              {index > 0 && <ChevronRight className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />}
              <div
                className={cn(
                  "flex items-center rounded-full border text-sm",
                  LINK_STATUS_TONE[link.status],
                  over === link.link_id && "ring-2 ring-primary",
                  dragging === link.link_id && "opacity-50"
                )}
              >
                <button
                  type="button"
                  onClick={() => onOpen(link)}
                  title={link.reason ?? undefined}
                  aria-label={`Step ${index + 1}: ${link.label}${
                    link.skill_version ? ` version ${link.skill_version}` : ""
                  }${target ? `, on ${target}` : ""}. ${linkStatusLabel(link)}${
                    link.reason ? `. ${link.reason}` : ""
                  }`}
                  className="flex min-h-9 items-center gap-1.5 whitespace-nowrap rounded-full py-1 pl-2.5 pr-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1"
                >
                  <span className="flex h-5 w-5 items-center justify-center rounded-full bg-foreground/10 text-[11px] font-bold">
                    {index + 1}
                  </span>
                  <span className="font-medium">{link.label}</span>
                  {link.skill_version && (
                    <span className="text-xs text-muted-foreground">v{link.skill_version}</span>
                  )}
                  {target && <span className="rounded bg-foreground/10 px-1 text-xs">{target}</span>}
                  {STATUS_ICON[link.status]}
                  <span className="text-xs">{linkStatusLabel(link)}</span>
                </button>
                {canRun && (
                  <button
                    type="button"
                    onClick={() => onRun(link)}
                    aria-label={`Run ${link.label}`}
                    className="mr-1 rounded-full px-2 py-1 text-xs font-semibold text-primary hover:bg-primary/10 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                  >
                    Run
                  </button>
                )}
                {movable && (
                  <span className="mr-1 flex items-center">
                    <button
                      type="button"
                      disabled={position <= 0}
                      onClick={() => onMove(link, -1)}
                      aria-label={`Move ${link.label} earlier`}
                      className="rounded p-1 hover:bg-foreground/10 disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      disabled={position === evaluations.length - 1}
                      onClick={() => onMove(link, 1)}
                      aria-label={`Move ${link.label} later`}
                      className="rounded p-1 hover:bg-foreground/10 disabled:opacity-30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => onRemove(link)}
                      aria-label={`Remove ${link.label} from the chain`}
                      className="rounded p-1 hover:bg-destructive/10 hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                    >
                      <X className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  </span>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {!readOnly && (
        <Button
          variant="outline"
          size="sm"
          onClick={onAdd}
          className="h-9 shrink-0 rounded-full"
          aria-label="Add a skill to the chain"
          aria-keyshortcuts="/"
        >
          <Plus className="mr-1 h-4 w-4" aria-hidden="true" />
          Skill
          <kbd className="ml-2 rounded border bg-muted px-1 text-[10px] font-medium text-muted-foreground">/</kbd>
        </Button>
      )}
    </nav>
  );
};

export default SkillRail;
