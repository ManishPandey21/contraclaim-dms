import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Lock, LockOpen, Save } from "lucide-react";
import type { DraftRunResponse } from "@/types/letterDrafting";

interface Props {
  run: DraftRunResponse;
  loading?: boolean;
  onSave: (lockedParagraphs: string[]) => Promise<void> | void;
}

const normalize = (text: string) => text.replace(/\s+/g, " ").trim().toLowerCase();

/**
 * Paragraph lock toggles: human-approved paragraphs are locked so AI redrafts
 * reproduce them verbatim (verified server-side, violations surfaced).
 */
const LockedParagraphsPanel = ({ run, loading, onSave }: Props) => {
  const draftBody = run.draft_artifact?.draft_letter ?? "";
  const paragraphs = useMemo(
    () =>
      draftBody
        .split(/\n{2,}/)
        .map((p) => p.trim())
        .filter((p) => p.length >= 40),
    [draftBody],
  );

  const [locked, setLocked] = useState<Set<number>>(new Set());
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);

  // Initialise toggles from the run's persisted locks.
  useEffect(() => {
    const persisted = new Set((run.locked_paragraphs ?? []).map(normalize));
    const next = new Set<number>();
    paragraphs.forEach((p, idx) => {
      if (persisted.has(normalize(p))) next.add(idx);
    });
    setLocked(next);
    setDirty(false);
  }, [paragraphs, run.locked_paragraphs]);

  if (!paragraphs.length) return null;

  const toggle = (idx: number) => {
    setLocked((prev) => {
      const next = new Set(prev);
      next.has(idx) ? next.delete(idx) : next.add(idx);
      return next;
    });
    setDirty(true);
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await onSave(paragraphs.filter((_, idx) => locked.has(idx)));
      setDirty(false);
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Lock className="h-4 w-4" />
          Locked paragraphs
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Locked paragraphs must survive AI redrafts verbatim; violations are
          surfaced as warnings after each revision.
        </p>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="max-h-72 space-y-2 overflow-auto pr-1">
          {paragraphs.map((paragraph, idx) => {
            const isLocked = locked.has(idx);
            return (
              <button
                key={idx}
                type="button"
                onClick={() => toggle(idx)}
                className={`w-full rounded-md border p-2 text-left text-xs transition-colors ${
                  isLocked
                    ? "border-amber-300 bg-amber-50"
                    : "hover:bg-muted/50"
                }`}
                title={isLocked ? "Unlock paragraph" : "Lock paragraph"}
              >
                <span className="mb-1 flex items-center gap-1 font-medium">
                  {isLocked ? (
                    <Lock className="h-3 w-3 text-amber-600" />
                  ) : (
                    <LockOpen className="h-3 w-3 text-gray-400" />
                  )}
                  Paragraph {idx + 1} {isLocked ? "(locked)" : ""}
                </span>
                <span className="line-clamp-3 text-muted-foreground">{paragraph}</span>
              </button>
            );
          })}
        </div>
        <div className="flex items-center justify-between">
          <p className="text-xs text-muted-foreground">
            {locked.size} of {paragraphs.length} locked
          </p>
          <Button size="sm" className="gap-2" onClick={handleSave} disabled={!dirty || saving || loading}>
            <Save className="h-4 w-4" />
            Save locks
          </Button>
        </div>
      </CardContent>
    </Card>
  );
};

export default LockedParagraphsPanel;
