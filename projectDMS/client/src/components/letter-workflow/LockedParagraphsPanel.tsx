import { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Lock, LockOpen, PenLine, Save, Sparkles } from "lucide-react";
import type {
  DraftRunResponse,
  SectionEditAction,
} from "@/types/letterDrafting";

interface Props {
  run: DraftRunResponse;
  loading?: boolean;
  onFreeze: (sectionIndices: number[], expectedDraftHash?: string) => Promise<void> | void;
  onRevise: (
    sectionIndices: number[],
    action: SectionEditAction,
    expectedDraftHash?: string,
  ) => Promise<void> | void;
}

const ACTIONS: { value: SectionEditAction; label: string }[] = [
  { value: "rewrite", label: "Rewrite" },
  { value: "grammar_spelling", label: "Grammar & spelling" },
  { value: "clarity_structure", label: "Clarity & structure" },
  { value: "tone_formality", label: "Tone & formality" },
  { value: "expand", label: "Expand" },
  { value: "polish", label: "Polish" },
];

async function sha256(text: string): Promise<string | undefined> {
  if (!globalThis.crypto?.subtle) return undefined;
  const bytes = new TextEncoder().encode(text);
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest))
    .map((value) => value.toString(16).padStart(2, "0"))
    .join("");
}

/**
 * Section-level edit boundary. Frozen sections are captured from the stored
 * draft by the server; only selected editable indices are sent for revision.
 */
const LockedParagraphsPanel = ({ run, loading, onFreeze, onRevise }: Props) => {
  const draftBody = run.draft_artifact?.draft_letter ?? "";
  const sections = useMemo(
    () =>
      draftBody
        .split(/(?:\r?\n[ \t]*){2,}/)
        .filter((section) => section.trim().length > 0),
    [draftBody],
  );

  const persistedFrozen = useMemo(
    () => new Set((run.frozen_sections ?? []).map((section) => section.section_index)),
    [run.frozen_sections],
  );
  const [frozen, setFrozen] = useState<Set<number>>(new Set());
  const [editable, setEditable] = useState<Set<number>>(new Set());
  const [action, setAction] = useState<SectionEditAction>("polish");
  const [dirty, setDirty] = useState(false);
  const [saving, setSaving] = useState(false);
  const [revising, setRevising] = useState(false);

  useEffect(() => {
    setFrozen(new Set(persistedFrozen));
    setEditable((current) => {
      const next = new Set<number>();
      current.forEach((index) => {
        if (!persistedFrozen.has(index) && index < sections.length) next.add(index);
      });
      return next;
    });
    setDirty(false);
  }, [persistedFrozen, sections.length]);

  if (!sections.length) return null;

  const toggleFrozen = (index: number) => {
    setFrozen((current) => {
      const next = new Set(current);
      next.has(index) ? next.delete(index) : next.add(index);
      return next;
    });
    setEditable((current) => {
      const next = new Set(current);
      next.delete(index);
      return next;
    });
    setDirty(true);
  };

  const toggleEditable = (index: number) => {
    if (frozen.has(index)) return;
    setEditable((current) => {
      const next = new Set(current);
      next.has(index) ? next.delete(index) : next.add(index);
      return next;
    });
  };

  const handleFreeze = async () => {
    setSaving(true);
    try {
      await onFreeze([...frozen].sort((a, b) => a - b), await sha256(draftBody));
      setDirty(false);
    } finally {
      setSaving(false);
    }
  };

  const handleRevise = async () => {
    if (!editable.size) return;
    setRevising(true);
    try {
      await onRevise(
        [...editable].sort((a, b) => a - b),
        action,
        await sha256(draftBody),
      );
      setEditable(new Set());
    } finally {
      setRevising(false);
    }
  };

  return (
    <Card>
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Lock className="h-4 w-4" />
          Freeze & edit sections
        </CardTitle>
        <p className="text-xs text-muted-foreground">
          Frozen content is preserved character-for-character. Select only the
          remaining sections the AI may rewrite.
        </p>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="max-h-80 space-y-2 overflow-auto pr-1">
          {sections.map((section, index) => {
            const isFrozen = frozen.has(index);
            const isEditable = editable.has(index);
            return (
              <div
                key={index}
                className={`rounded-md border p-2 text-xs ${
                  isFrozen
                    ? "border-amber-300 bg-amber-50"
                    : isEditable
                      ? "border-blue-300 bg-blue-50"
                      : ""
                }`}
              >
                <div className="mb-2 flex items-center justify-between gap-2">
                  <span className="font-medium">Section {index + 1}</span>
                  <div className="flex gap-1">
                    <Button
                      type="button"
                      size="sm"
                      variant={isFrozen ? "default" : "outline"}
                      className="h-7 gap-1 px-2 text-xs"
                      onClick={() => toggleFrozen(index)}
                    >
                      {isFrozen ? <Lock className="h-3 w-3" /> : <LockOpen className="h-3 w-3" />}
                      {isFrozen ? "Frozen" : "Freeze"}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant={isEditable ? "default" : "outline"}
                      className="h-7 gap-1 px-2 text-xs"
                      onClick={() => toggleEditable(index)}
                      disabled={isFrozen}
                    >
                      <PenLine className="h-3 w-3" />
                      {isEditable ? "Editable" : "Edit"}
                    </Button>
                  </div>
                </div>
                <p className="line-clamp-4 whitespace-pre-wrap text-muted-foreground">
                  {section}
                </p>
              </div>
            );
          })}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="gap-2"
            onClick={handleFreeze}
            disabled={!dirty || saving || revising || loading}
          >
            <Save className="h-4 w-4" />
            Save frozen sections
          </Button>
          <select
            aria-label="Section edit action"
            className="h-9 rounded-md border bg-background px-2 text-sm"
            value={action}
            onChange={(event) => setAction(event.target.value as SectionEditAction)}
            disabled={revising || loading}
          >
            {ACTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
          <Button
            size="sm"
            className="gap-2"
            onClick={handleRevise}
            disabled={!editable.size || saving || revising || loading}
          >
            <Sparkles className="h-4 w-4" />
            Revise {editable.size || ""} selected
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          {frozen.size} frozen · {editable.size} selected for AI editing · all
          other sections remain unchanged for this revision.
        </p>
      </CardContent>
    </Card>
  );
};

export default LockedParagraphsPanel;
