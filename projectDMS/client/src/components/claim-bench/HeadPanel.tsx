import React, { useEffect, useState } from "react";
import { Loader2, Play, RotateCw, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import {
  CLASSIFICATION_LABEL,
  CLASSIFICATION_OPTIONS,
  ELEMENT_LABEL,
  ELEMENT_QUESTION,
  KIND_LABEL,
  STATE_LABEL,
  STATE_OPTIONS,
  differsFromProposal,
  parseAmount,
  parseDays,
  proposalSource,
  testSummary,
} from "@/lib/claim-bench-logic";
import { parseClauseList } from "@/lib/claims-helpers";
import { formatCrore } from "@/lib/format-in";
import {
  ELEMENT_KEYS,
  type ClaimHead,
  type ElementKey,
  type ElementState,
  type HeadClassification,
  type HeadKind,
  type HeadPatch,
} from "@/types/claimBench";

interface Props {
  head: ClaimHead;
  currency?: string | null;
  readOnly: boolean;
  busy: boolean;
  testing: boolean;
  canTest: boolean;
  cannotTestReason?: string;
  onPatch: (patch: HeadPatch) => void;
  onDecide: (element: ElementKey, decision: { state?: ElementState; clear?: boolean; note?: string }) => void;
  onTest: (force: boolean) => void;
  onRemove: () => void;
}

const STATE_STYLE: Record<ElementState, string> = {
  supported: "border-green-700 bg-green-700 text-white",
  weak: "border-amber-700 bg-amber-700 text-white",
  missing: "border-red-700 bg-red-700 text-white",
  not_applicable: "border-slate-600 bg-slate-600 text-white",
};

const PROPOSED_TEXT: Record<ElementState, string> = {
  supported: "text-green-800",
  weak: "text-amber-800",
  missing: "text-red-800",
  not_applicable: "text-slate-700",
};

/**
 * One head of claim: its facts, the five elements with what was proposed and
 * what the person decided, and its classification. A proposal is shown beside
 * the decision buttons, never inside them, so it cannot be mistaken for a call.
 */
const HeadPanel: React.FC<Props> = ({
  head,
  currency,
  readOnly,
  busy,
  testing,
  canTest,
  cannotTestReason,
  onPatch,
  onDecide,
  onTest,
  onRemove,
}) => {
  const [form, setForm] = useState({ title: "", summary: "", clauses: "", amount: "", days: "" });
  const [notes, setNotes] = useState<Partial<Record<ElementKey, string>>>({});

  useEffect(() => {
    setForm({
      title: head.title,
      summary: head.event_summary ?? "",
      clauses: head.clause_refs.join(", "),
      amount: head.amount_claimed != null ? String(head.amount_claimed) : "",
      days: head.eot_days_claimed != null ? String(head.eot_days_claimed) : "",
    });
    setNotes({});
    // Reset the form when another head is selected or the server's copy changes.
  }, [head.head_id, head.title, head.event_summary, head.clause_refs, head.amount_claimed, head.eot_days_claimed]);

  const amount = parseAmount(form.amount);
  const days = parseDays(form.days);
  const clauses = parseClauseList(form.clauses);
  const dirty =
    form.title.trim() !== head.title ||
    form.summary.trim() !== (head.event_summary ?? "") ||
    clauses.join("|") !== head.clause_refs.join("|") ||
    (amount ?? null) !== (head.amount_claimed ?? null) ||
    (days ?? null) !== (head.eot_days_claimed ?? null);
  const valid = form.title.trim() !== "" && amount !== undefined && days !== undefined;

  const save = () => {
    const patch: HeadPatch = {
      title: form.title.trim(),
      event_summary: form.summary.trim(),
      clause_refs: clauses,
    };
    if (amount === null) patch.clear_amount = true;
    else if (amount !== undefined) patch.amount_claimed = amount;
    if (days === null) patch.clear_eot_days = true;
    else if (days !== undefined) patch.eot_days_claimed = days;
    onPatch(patch);
  };

  const disabled = readOnly || busy;

  return (
    <div className="space-y-4">
      <section aria-labelledby="head-facts-heading" className="space-y-3 rounded-lg border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="head-facts-heading" className="text-sm font-semibold">
            {head.head_id} · What is claimed
          </h2>
          <div className="flex items-center gap-2">
            <label htmlFor="head-kind" className="text-xs text-muted-foreground">
              Claims
            </label>
            <select
              id="head-kind"
              className="h-8 rounded-md border border-input bg-background px-2 text-sm"
              value={head.kind}
              disabled={disabled}
              onChange={(e) => onPatch({ kind: e.target.value as HeadKind })}
            >
              {(Object.keys(KIND_LABEL) as HeadKind[]).map((kind) => (
                <option key={kind} value={kind}>
                  {KIND_LABEL[kind]}
                </option>
              ))}
            </select>
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              onClick={() => {
                if (window.confirm(`Remove head ${head.head_id} "${head.title}"? Its decisions are removed with it.`)) onRemove();
              }}
            >
              <Trash2 className="mr-1 h-4 w-4" aria-hidden="true" />
              Remove
            </Button>
          </div>
        </div>
        <div>
          <Label htmlFor="head-title">Head of claim</Label>
          <Input id="head-title" value={form.title} disabled={disabled} onChange={(e) => setForm({ ...form, title: e.target.value })} />
        </div>
        <div>
          <Label htmlFor="head-summary">What happened</Label>
          <Textarea id="head-summary" rows={3} value={form.summary} disabled={disabled} onChange={(e) => setForm({ ...form, summary: e.target.value })} />
        </div>
        <div className="grid gap-3 sm:grid-cols-3">
          <div>
            <Label htmlFor="head-clauses">Clauses relied on</Label>
            <Input id="head-clauses" value={form.clauses} placeholder="e.g. 8.4, 20.1" disabled={disabled} onChange={(e) => setForm({ ...form, clauses: e.target.value })} />
          </div>
          <div>
            <Label htmlFor="head-days">Days claimed</Label>
            <Input id="head-days" inputMode="numeric" value={form.days} disabled={disabled} aria-invalid={days === undefined} onChange={(e) => setForm({ ...form, days: e.target.value })} />
            {days === undefined && <p className="pt-1 text-xs text-red-700">Whole days only.</p>}
          </div>
          <div>
            <Label htmlFor="head-amount">Amount claimed</Label>
            <Input id="head-amount" inputMode="decimal" value={form.amount} disabled={disabled} aria-invalid={amount === undefined} onChange={(e) => setForm({ ...form, amount: e.target.value })} />
            {amount === undefined ? (
              <p className="pt-1 text-xs text-red-700">Enter a number.</p>
            ) : (
              amount != null && <p className="pt-1 text-xs text-muted-foreground">{formatCrore(amount, currency)}</p>
            )}
          </div>
        </div>
        {dirty && (
          <div className="flex items-center gap-3">
            <Button size="sm" disabled={disabled || !valid} onClick={save}>
              Save changes
            </Button>
            {head.step_id && (
              <p className="text-xs text-muted-foreground">Saving marks this head's test as out of date.</p>
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="head-elements-heading" className="space-y-3 rounded-lg border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h2 id="head-elements-heading" className="text-sm font-semibold">
              The five elements
            </h2>
            <p className={`text-xs ${head.test_stale ? "font-medium text-amber-800" : "text-muted-foreground"}`}>
              {testSummary(head)}
            </p>
          </div>
          <Button
            size="sm"
            variant={head.step_id && !head.test_stale ? "outline" : "default"}
            disabled={disabled || testing || !canTest || dirty}
            title={dirty ? "Save your changes first" : cannotTestReason}
            onClick={() => onTest(Boolean(head.step_id) && !head.test_stale)}
          >
            {testing ? (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden="true" />
            ) : head.step_id ? (
              <RotateCw className="mr-2 h-4 w-4" aria-hidden="true" />
            ) : (
              <Play className="mr-2 h-4 w-4" aria-hidden="true" />
            )}
            {head.step_id ? "Test again" : "Test this head"}
          </Button>
        </div>

        <ul className="divide-y rounded-md border">
          {ELEMENT_KEYS.map((key) => {
            const element = head.elements[key];
            const note = notes[key] ?? element?.note ?? "";
            return (
              <li key={key} className="space-y-2 p-3">
                <div className="flex flex-wrap items-start justify-between gap-3">
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{ELEMENT_LABEL[key]}</p>
                    <p className="text-xs text-muted-foreground">{ELEMENT_QUESTION[key]}</p>
                  </div>
                  <div role="group" aria-label={`Your call on ${ELEMENT_LABEL[key]}`} className="flex flex-wrap gap-1">
                    {STATE_OPTIONS.map((state) => {
                      const selected = element?.state === state;
                      return (
                        <button
                          key={state}
                          type="button"
                          aria-pressed={selected}
                          disabled={disabled}
                          onClick={() => onDecide(key, selected ? { clear: true } : { state })}
                          className={`rounded-md border px-2.5 py-1 text-xs font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
                            selected ? STATE_STYLE[state] : "border-input bg-background hover:bg-muted"
                          }`}
                        >
                          {STATE_LABEL[state]}
                        </button>
                      );
                    })}
                  </div>
                </div>
                {element?.proposed_state ? (
                  <div className="rounded-md bg-muted/50 p-2 text-sm">
                    <p>
                      <span className="text-xs uppercase text-muted-foreground">{proposalSource(element)}: </span>
                      <span className={`font-medium ${PROPOSED_TEXT[element.proposed_state]}`}>
                        {STATE_LABEL[element.proposed_state]}
                      </span>
                      {differsFromProposal(element) && (
                        <span className="ml-2 text-xs text-muted-foreground">(your call differs)</span>
                      )}
                    </p>
                    {element.proposed_reason && <p className="text-muted-foreground">{element.proposed_reason}</p>}
                  </div>
                ) : (
                  <p className="text-xs text-muted-foreground">Not tested. You can still record your call.</p>
                )}
                {element?.state && (
                  <div className="flex items-end gap-2">
                    <div className="flex-1">
                      <Label htmlFor={`note-${key}`} className="text-xs">
                        Your reason (optional)
                      </Label>
                      <Input
                        id={`note-${key}`}
                        value={note}
                        disabled={disabled}
                        onChange={(e) => setNotes({ ...notes, [key]: e.target.value })}
                      />
                    </div>
                    {note !== (element.note ?? "") && (
                      <Button size="sm" variant="outline" disabled={disabled} onClick={() => onDecide(key, { state: element.state!, note })}>
                        Save reason
                      </Button>
                    )}
                  </div>
                )}
              </li>
            );
          })}
        </ul>

        {(head.missing_information.length > 0 || head.residual_risks.length > 0) && (
          <div className="grid gap-3 sm:grid-cols-2">
            {head.missing_information.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold uppercase text-muted-foreground">Still needed</h3>
                <ul className="list-disc pl-5 text-sm">
                  {head.missing_information.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
            {head.residual_risks.length > 0 && (
              <div>
                <h3 className="text-xs font-semibold uppercase text-muted-foreground">Residual risks</h3>
                <ul className="list-disc pl-5 text-sm">
                  {head.residual_risks.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            )}
          </div>
        )}
      </section>

      <section aria-labelledby="head-classification-heading" className="space-y-2 rounded-lg border bg-card p-4">
        <h2 id="head-classification-heading" className="text-sm font-semibold">
          Classification
        </h2>
        <div role="group" aria-label="Classification of this head" className="flex flex-wrap gap-2">
          {CLASSIFICATION_OPTIONS.map((option) => {
            const selected = head.classification === option;
            return (
              <button
                key={option}
                type="button"
                aria-pressed={selected}
                disabled={disabled}
                onClick={() =>
                  onPatch(selected ? { clear_classification: true } : { classification: option as HeadClassification })
                }
                className={`rounded-md border px-3 py-1.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:opacity-50 ${
                  selected ? "border-slate-800 bg-slate-800 text-white" : "border-input bg-background hover:bg-muted"
                }`}
              >
                {CLASSIFICATION_LABEL[option]}
              </button>
            );
          })}
        </div>
        {head.proposed_classification && head.proposed_classification !== "undetermined" && (
          <p className="text-xs text-muted-foreground">
            Proposed: {CLASSIFICATION_LABEL[head.proposed_classification]}
          </p>
        )}
      </section>
    </div>
  );
};

export default HeadPanel;
