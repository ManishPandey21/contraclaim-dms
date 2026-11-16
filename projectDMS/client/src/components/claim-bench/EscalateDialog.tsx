import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/hooks/useLetterBench";
import { listArbitrationCases, type ArbitrationCase } from "@/services/arbitration-cases-api";
import {
  previewEscalation,
  runEscalation,
  type EscalationPreview,
  type EscalationResult,
} from "@/services/claim-escalation-api";

interface Props {
  claimId: string;
  claimTitle: string;
  projectId?: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onEscalated: () => void;
}

const MATRIX_LABEL: Record<string, string> = {
  "claim-matrix": "Claim matrix (heads of claim)",
  "notice-compliance": "Notice compliance",
  "quantum-annexures": "Quantum annexures (cost lines with proof)",
  "chronology-matrix": "Chronology",
  "document-index": "Document index",
  "issue-matrix": "Open issues",
};

const select = "h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50";
const NEW = "__new__";

/**
 * Confirmation sheet for escalating a claim. It shows exactly what would be
 * carried into the case, what is weak, and what is left behind and why, before
 * anything is changed.
 */
const EscalateDialog: React.FC<Props> = ({ claimId, claimTitle, projectId, open, onOpenChange, onEscalated }) => {
  const [preview, setPreview] = useState<EscalationPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [cases, setCases] = useState<ArbitrationCase[]>([]);
  const [target, setTarget] = useState(NEW);
  const [title, setTitle] = useState("");
  const [perspective, setPerspective] = useState<"claimant" | "respondent">("claimant");
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<EscalationResult | null>(null);

  useEffect(() => {
    if (!open) return;
    let active = true;
    setPreview(null);
    setError(null);
    setResult(null);
    setTarget(NEW);
    setTitle(claimTitle);
    previewEscalation([claimId])
      .then((data) => active && setPreview(data))
      .catch((reason) => active && setError(errorMessage(reason, "The escalation could not be previewed.")));
    listArbitrationCases(projectId ? { project_id: projectId } : {})
      .then((data) => active && setCases((data || []).filter((item) => !projectId || item.project_id === projectId)))
      .catch(() => active && setCases([]));
    return () => {
      active = false;
    };
  }, [open, claimId, claimTitle, projectId]);

  const carry = preview?.claims[0];
  const ready = Boolean(preview?.can_escalate) && (target !== NEW || title.trim() !== "");

  const escalate = async () => {
    setBusy(true);
    try {
      const done = await runEscalation(
        [claimId],
        target === NEW ? { new_case: { title: title.trim(), party_perspective: perspective } } : { case_id: target }
      );
      setResult(done);
      onEscalated();
    } catch (reason) {
      toast.error(errorMessage(reason, "The claim could not be escalated"));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] max-w-2xl overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Escalate to arbitration</DialogTitle>
          <DialogDescription>
            Carries what this claim's record establishes into an arbitration case. Every row arrives as “needs review”
            and passes the case's own review before it can support a pleading.
          </DialogDescription>
        </DialogHeader>

        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        {!preview && !error && <p className="text-sm text-muted-foreground">Working out what would be carried over…</p>}

        {result ? (
          <div className="space-y-3 text-sm">
            <p role="status" className="rounded-md border border-green-700 bg-green-50 p-3 text-green-950">
              {result.created_case ? "Case created" : "Case extended"}: <strong>{result.case_title}</strong>.{" "}
              {Object.values(result.rows_created).reduce((sum, count) => sum + count, 0)} rows added, all awaiting
              review.
              {result.rows_already_present > 0 && ` ${result.rows_already_present} were already there and were not added again.`}
            </p>
            <p>The claim's evidence is now frozen, so its board can no longer be changed.</p>
            <Button asChild>
              <Link to={`/arbitration/cases/${result.case_id}`}>Open the case</Link>
            </Button>
          </div>
        ) : (
          preview &&
          carry && (
            <div className="space-y-4 text-sm">
              {preview.blockers.length > 0 && (
                <ul role="alert" className="list-disc space-y-1 rounded-md border border-red-700 bg-red-50 p-3 pl-7 text-red-950">
                  {preview.blockers.map((blocker) => (
                    <li key={blocker}>{blocker}</li>
                  ))}
                </ul>
              )}

              <section aria-labelledby="carried-heading" className="space-y-1">
                <h3 id="carried-heading" className="font-semibold">
                  What will be carried over
                </h3>
                <ul className="divide-y rounded-md border">
                  {Object.entries(preview.totals).map(([slug, count]) => (
                    <li key={slug} className="flex justify-between px-3 py-1.5">
                      <span>{MATRIX_LABEL[slug] ?? slug}</span>
                      <span className="font-medium">
                        {count} row{count === 1 ? "" : "s"}
                      </span>
                    </li>
                  ))}
                </ul>
              </section>

              {carry.weak.length > 0 && (
                <section aria-labelledby="weak-heading" className="space-y-1">
                  <h3 id="weak-heading" className="font-semibold">
                    Carried, but weak on the record
                  </h3>
                  <ul className="list-disc space-y-0.5 rounded-md border border-amber-600 bg-amber-50 p-3 pl-7 text-amber-950">
                    {carry.weak.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </section>
              )}

              {carry.not_carried.length > 0 && (
                <section aria-labelledby="left-heading" className="space-y-1">
                  <h3 id="left-heading" className="font-semibold">
                    Left behind
                  </h3>
                  <ul className="list-disc space-y-0.5 rounded-md border p-3 pl-7">
                    {carry.not_carried.map((item) => (
                      <li key={item}>{item}</li>
                    ))}
                  </ul>
                </section>
              )}

              <p className="text-xs text-muted-foreground">
                Internal analysis (simulations and the strategy note) is never carried into a case.
                {carry.status_after !== carry.status
                  ? ` The claim's status becomes “${carry.status_after}”.`
                  : " The claim's status does not change."}{" "}
                Its evidence is frozen and its board becomes read-only.
              </p>

              <section className="grid gap-3 sm:grid-cols-2">
                <div className="space-y-1 sm:col-span-2">
                  <Label htmlFor="escalate-target">Arbitration case</Label>
                  <select id="escalate-target" className={select} value={target} onChange={(event) => setTarget(event.target.value)}>
                    <option value={NEW}>Create a new case</option>
                    {cases.map((item) => (
                      <option key={item._id} value={item._id}>
                        Add to: {item.title}
                      </option>
                    ))}
                  </select>
                </div>
                {target === NEW && (
                  <>
                    <div className="space-y-1">
                      <Label htmlFor="escalate-title">Case title</Label>
                      <Input id="escalate-title" value={title} onChange={(event) => setTitle(event.target.value)} />
                    </div>
                    <div className="space-y-1">
                      <Label htmlFor="escalate-perspective">This side is the</Label>
                      <select
                        id="escalate-perspective"
                        className={select}
                        value={perspective}
                        onChange={(event) => setPerspective(event.target.value as "claimant" | "respondent")}
                      >
                        <option value="claimant">Claimant</option>
                        <option value="respondent">Respondent</option>
                      </select>
                    </div>
                  </>
                )}
              </section>

              <div className="flex justify-end gap-2">
                <Button variant="outline" onClick={() => onOpenChange(false)} disabled={busy}>
                  Cancel
                </Button>
                <Button onClick={escalate} disabled={busy || !ready}>
                  Escalate
                </Button>
              </div>
            </div>
          )
        )}
      </DialogContent>
    </Dialog>
  );
};

export default EscalateDialog;
