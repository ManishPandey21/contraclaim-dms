import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Lock, LockOpen, Pencil, Plus, Trash2 } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useHeadAnalysis } from "@/hooks/useClaimBench";
import { parseAmount } from "@/lib/claim-bench-logic";
import { CATEGORY_LABEL, FORMULA_INPUTS, FORMULA_LABEL, RECORD_ROLE_LABEL, missingFormulaFigures } from "@/lib/delay-lab-logic";
import { formatCrore, formatDateIN, formatINR, formatPaise, fromDateInput, toDateInput } from "@/lib/format-in";
import { listEntityDocumentLinks } from "@/services/document-relationships-api";
import type {
  QuantumCategory,
  QuantumFormula,
  QuantumLine,
  QuantumLineInput,
  RecordRole,
} from "@/types/claimBench";

interface Props {
  claimId: string;
  headId: string;
  boardVersion: number;
  readOnly: boolean;
}

interface Draft {
  lineId?: string;
  category: QuantumCategory;
  description: string;
  amount: string;
  period_start: string;
  period_end: string;
  formula: QuantumFormula;
  basis: string;
  records: { document_id: string; role: RecordRole }[];
  /** Formula figures by key: the value as typed, and the record it is taken from. */
  inputs: Record<string, { value: string; document_id: string }>;
}

const EMPTY: Draft = {
  category: "direct",
  description: "",
  amount: "",
  period_start: "",
  period_end: "",
  formula: "none",
  basis: "",
  records: [],
  inputs: {},
};

const select = "h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50";
const PERIOD = new Set<QuantumCategory>(["prolongation", "overhead_formula"]);

const toDraft = (line: QuantumLine): Draft => ({
  lineId: line.line_id,
  category: line.category,
  description: line.description,
  amount: String(line.amount),
  period_start: toDateInput(line.period_start),
  period_end: toDateInput(line.period_end),
  formula: line.formula,
  basis: line.basis ?? "",
  records: line.records.map((record) => ({ ...record })),
  inputs: Object.fromEntries(
    Object.entries(line.formula_inputs ?? {}).map(([key, input]) => [
      key,
      { value: input.value, document_id: input.document_id ?? "" },
    ])
  ),
});

/**
 * The amount of a head as a build-up of lines. A line stays locked, and out of
 * the supported total, until what its kind of cost requires is linked. The
 * lock reasons are the server's; this screen only shows them.
 */
const QuantumTab: React.FC<Props> = ({ claimId, headId, boardVersion, readOnly }) => {
  const { quantum, saveLine, removeLine } = useHeadAnalysis(claimId, headId, boardVersion);
  const [draft, setDraft] = useState<Draft | null>(null);
  const documents = useQuery({
    queryKey: ["claim-bench", "claim-documents", claimId],
    queryFn: () => listEntityDocumentLinks("claim", claimId),
    enabled: draft !== null,
    retry: false,
  });
  const view = quantum.data;
  if (quantum.isLoading) return <p className="p-4 text-sm text-muted-foreground">Loading the quantum build-up…</p>;
  if (!view) return <p role="alert" className="p-4 text-sm text-destructive">The quantum build-up could not be loaded.</p>;

  const disabled = readOnly || saveLine.isPending || removeLine.isPending;
  const amount = draft ? parseAmount(draft.amount) : null;
  // A formula line's amount is computed on the server from its figures; it is never typed.
  const isFormula = draft !== null && draft.category === "overhead_formula" && draft.formula !== "none";
  const missingFigures = draft ? missingFormulaFigures(draft.category, draft.formula, draft.inputs) : [];
  const ready =
    draft !== null &&
    draft.description.trim() !== "" &&
    (isFormula ? missingFigures.length === 0 : typeof amount === "number");
  const docs = (documents.data ?? []).map((link) => ({
    id: String(link.document_id),
    name: link.document?.subject || link.document?.filename || String(link.document_id),
  }));
  const nameOf = (id: string) => docs.find((doc) => doc.id === id)?.name ?? id;

  const submit = () => {
    if (!draft || !ready) return;
    const formulaInputs = isFormula
      ? Object.fromEntries(
          FORMULA_INPUTS[draft.formula as Exclude<QuantumFormula, "none">].map(({ key }) => [
            key,
            {
              value: String(parseAmount(draft.inputs[key]?.value ?? "")),
              document_id: draft.inputs[key]?.document_id || null,
            },
          ])
        )
      : {};
    const line: QuantumLineInput = {
      category: draft.category,
      description: draft.description.trim(),
      ...(isFormula ? {} : { amount: amount as number }),
      formula_inputs: formulaInputs,
      period_start: PERIOD.has(draft.category) ? fromDateInput(draft.period_start) ?? null : null,
      period_end: PERIOD.has(draft.category) ? fromDateInput(draft.period_end) ?? null : null,
      formula: draft.category === "overhead_formula" ? draft.formula : "none",
      basis: draft.basis.trim() || null,
      records: draft.records,
    };
    saveLine.mutate({ line, lineId: draft.lineId }, { onSuccess: () => setDraft(null) });
  };

  const toggleRecord = (documentId: string, role: RecordRole | "") =>
    setDraft((current) =>
      current && {
        ...current,
        records: [
          ...current.records.filter((record) => record.document_id !== documentId),
          ...(role ? [{ document_id: documentId, role }] : []),
        ],
      }
    );

  return (
    <div className="space-y-4">
      <section aria-labelledby="quantum-totals-heading" className="space-y-2 rounded-lg border bg-card p-4">
        <h2 id="quantum-totals-heading" className="text-sm font-semibold">
          Build-up of this head
        </h2>
        <dl className="grid grid-cols-3 gap-2 text-sm">
          {(
            [
              ["Claimed", view.claimed_total, view.claimed_total_paise, "text-foreground"],
              ["Supported", view.supported_total, view.supported_total_paise, "text-green-800"],
              ["Locked", view.locked_total, view.locked_total_paise, view.locked_total > 0 ? "text-red-800" : "text-muted-foreground"],
            ] as [string, number, number | undefined, string][]
          ).map(([label, value, paise, tone]) => (
            <div key={label} className="rounded-md bg-muted/50 p-2">
              <dt className="text-xs text-muted-foreground">{label}</dt>
              {/* The paise figure is authoritative; the rupee float is for older servers only. */}
              <dd className={`font-semibold tabular-nums ${tone}`}>
                {paise != null ? formatPaise(paise, view.currency) : formatINR(value, view.currency)}
              </dd>
              <dd className="text-xs text-muted-foreground">
                {formatCrore(paise != null ? paise / 100 : value, view.currency)}
              </dd>
            </div>
          ))}
        </dl>
        {(view.calculated_total_paise ?? 0) > 0 && (
          <p className="text-xs text-muted-foreground">
            Of the amount claimed, {formatPaise(view.calculated_total_paise, view.currency)} is calculated by a formula. A
            calculation is not a finding that the amount is recoverable.
          </p>
        )}
        <p className="text-xs text-muted-foreground">
          {view.lines.length === 0
            ? "No lines yet. Until there is a build-up, the head's amount is the figure typed on the Elements tab."
            : "The head's amount is the total of these lines. A locked line is claimed but not yet supported."}
        </p>
      </section>

      <section aria-labelledby="quantum-lines-heading" className="space-y-3 rounded-lg border bg-card p-4">
        <div className="flex items-center justify-between">
          <h2 id="quantum-lines-heading" className="text-sm font-semibold">
            Lines ({view.lines.length})
          </h2>
          <Button size="sm" variant="outline" disabled={disabled || draft !== null} onClick={() => setDraft({ ...EMPTY })}>
            <Plus className="mr-2 h-4 w-4" aria-hidden="true" />
            Add a line
          </Button>
        </div>
        <ul className="space-y-2">
          {view.lines.map(({ line, locked, lock_reasons, calculation_note }) => (
            <li key={line.line_id} className={`space-y-1 rounded-md border p-3 ${locked ? "border-red-700 bg-red-50/40" : ""}`}>
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div className="min-w-0">
                  <p className="flex items-center gap-2 text-sm font-medium">
                    {locked ? (
                      <Lock className="h-4 w-4 text-red-800" aria-label="Locked" />
                    ) : (
                      <LockOpen className="h-4 w-4 text-green-800" aria-label="Supported" />
                    )}
                    {line.description}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {line.line_id} · {CATEGORY_LABEL[line.category]}
                    {line.formula !== "none" && ` · ${FORMULA_LABEL[line.formula]}`}
                    {line.period_start && line.period_end && ` · ${formatDateIN(line.period_start)} to ${formatDateIN(line.period_end)}`}
                    {` · ${line.records.length} record${line.records.length === 1 ? "" : "s"}`}
                  </p>
                </div>
                <div className="flex items-center gap-1">
                  <span className="mr-2 text-sm font-semibold tabular-nums">
                    {line.amount_paise != null ? formatPaise(line.amount_paise, view.currency) : formatINR(line.amount, view.currency)}
                  </span>
                  <Button size="sm" variant="ghost" disabled={disabled || draft !== null} aria-label={`Edit ${line.line_id}`} onClick={() => setDraft(toDraft(line))}>
                    <Pencil className="h-4 w-4" aria-hidden="true" />
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    disabled={disabled}
                    aria-label={`Remove ${line.line_id}`}
                    onClick={() => window.confirm(`Remove line ${line.line_id}?`) && removeLine.mutate(line.line_id)}
                  >
                    <Trash2 className="h-4 w-4" aria-hidden="true" />
                  </Button>
                </div>
              </div>
              {locked && (
                <ul className="list-disc pl-9 text-xs text-red-900">
                  {lock_reasons.map((reason) => (
                    <li key={reason}>{reason}</li>
                  ))}
                </ul>
              )}
              {calculation_note && <p className="pl-6 text-xs text-muted-foreground">{calculation_note}</p>}
            </li>
          ))}
        </ul>

        {draft && (
          <div className="space-y-3 rounded-md border bg-muted/30 p-3">
            <h3 className="text-sm font-semibold">{draft.lineId ? `Edit ${draft.lineId}` : "New line"}</h3>
            <div className="grid gap-3 sm:grid-cols-3">
              <div>
                <Label htmlFor="q-category" className="text-xs">Kind of cost</Label>
                <select id="q-category" className={select} value={draft.category} onChange={(e) => setDraft({ ...draft, category: e.target.value as QuantumCategory })}>
                  {(Object.keys(CATEGORY_LABEL) as QuantumCategory[]).map((key) => (
                    <option key={key} value={key}>{CATEGORY_LABEL[key]}</option>
                  ))}
                </select>
              </div>
              <div className="sm:col-span-2">
                <Label htmlFor="q-description" className="text-xs">Description</Label>
                <Input id="q-description" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} />
              </div>
              <div>
                <Label htmlFor="q-amount" className="text-xs">Amount</Label>
                {isFormula ? (
                  <p id="q-amount" className="pt-2 text-xs text-muted-foreground">
                    Worked out from the formula's figures when the line is saved.
                  </p>
                ) : (
                  <>
                    <Input id="q-amount" inputMode="decimal" value={draft.amount} aria-invalid={amount === undefined} onChange={(e) => setDraft({ ...draft, amount: e.target.value })} />
                    {typeof amount === "number" && <p className="pt-1 text-xs text-muted-foreground">{formatCrore(amount, view.currency)}</p>}
                  </>
                )}
              </div>
              {PERIOD.has(draft.category) && (
                <>
                  <div>
                    <Label htmlFor="q-start" className="text-xs">Period from</Label>
                    <Input id="q-start" type="date" value={draft.period_start} onChange={(e) => setDraft({ ...draft, period_start: e.target.value })} />
                  </div>
                  <div>
                    <Label htmlFor="q-end" className="text-xs">Period to</Label>
                    <Input id="q-end" type="date" value={draft.period_end} onChange={(e) => setDraft({ ...draft, period_end: e.target.value })} />
                  </div>
                </>
              )}
              {draft.category === "overhead_formula" && (
                <div>
                  <Label htmlFor="q-formula" className="text-xs">Formula</Label>
                  <select id="q-formula" className={select} value={draft.formula} onChange={(e) => setDraft({ ...draft, formula: e.target.value as QuantumFormula })}>
                    {(Object.keys(FORMULA_LABEL) as QuantumFormula[]).map((key) => (
                      <option key={key} value={key}>{FORMULA_LABEL[key]}</option>
                    ))}
                  </select>
                </div>
              )}
              {isFormula && (
                <fieldset className="space-y-2 sm:col-span-3">
                  <legend className="text-xs font-medium">
                    {FORMULA_LABEL[draft.formula]} figures, each from a record linked to this claim
                  </legend>
                  {FORMULA_INPUTS[draft.formula as Exclude<QuantumFormula, "none">].map(({ key, label }) => {
                    const input = draft.inputs[key] ?? { value: "", document_id: "" };
                    const setInput = (next: Partial<typeof input>) =>
                      setDraft({ ...draft, inputs: { ...draft.inputs, [key]: { ...input, ...next } } });
                    return (
                      <div key={key} className="grid gap-2 sm:grid-cols-2">
                        <div>
                          <Label htmlFor={`q-input-${key}`} className="text-xs">{label}</Label>
                          <Input id={`q-input-${key}`} inputMode="decimal" value={input.value} onChange={(e) => setInput({ value: e.target.value })} />
                        </div>
                        <div>
                          <Label htmlFor={`q-input-${key}-record`} className="text-xs">Taken from</Label>
                          <select id={`q-input-${key}-record`} className={select} value={input.document_id} onChange={(e) => setInput({ document_id: e.target.value })}>
                            <option value="">No record yet (the line stays locked)</option>
                            {docs.map((doc) => (
                              <option key={doc.id} value={doc.id}>{doc.name}</option>
                            ))}
                          </select>
                        </div>
                      </div>
                    );
                  })}
                  {missingFigures.length > 0 && (
                    <p className="text-xs text-muted-foreground">Still needed: {missingFigures.join("; ")}.</p>
                  )}
                </fieldset>
              )}
              <div className="sm:col-span-3">
                <Label htmlFor="q-basis" className="text-xs">Basis (clause, rate or how it is calculated)</Label>
                <Input id="q-basis" value={draft.basis} onChange={(e) => setDraft({ ...draft, basis: e.target.value })} />
              </div>
            </div>
            <div>
              <p className="text-xs font-medium">Records, from the documents linked to this claim</p>
              {documents.isLoading && <p className="text-xs text-muted-foreground">Loading documents…</p>}
              {documents.data && docs.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  No documents are linked to this claim. Link them on the claim's documents page first; a line cannot
                  cite a document that is not part of the claim.
                </p>
              )}
              <ul className="mt-1 space-y-1">
                {docs.map((doc) => {
                  const current = draft.records.find((record) => record.document_id === doc.id)?.role ?? "";
                  return (
                    <li key={doc.id} className="flex items-center justify-between gap-2 rounded-md border bg-white p-2 text-sm">
                      <span className="min-w-0 truncate">{nameOf(doc.id)}</span>
                      <select
                        aria-label={`How ${doc.name} is used`}
                        className="h-8 rounded-md border border-input bg-background px-2 text-xs"
                        value={current}
                        onChange={(e) => toggleRecord(doc.id, e.target.value as RecordRole | "")}
                      >
                        <option value="">Not used</option>
                        {(Object.keys(RECORD_ROLE_LABEL) as RecordRole[]).map((role) => (
                          <option key={role} value={role}>{RECORD_ROLE_LABEL[role]}</option>
                        ))}
                      </select>
                    </li>
                  );
                })}
              </ul>
            </div>
            <div className="flex gap-2">
              <Button size="sm" disabled={disabled || !ready} onClick={submit}>
                {draft.lineId ? "Save line" : "Add line"}
              </Button>
              <Button size="sm" variant="ghost" onClick={() => setDraft(null)}>
                Cancel
              </Button>
            </div>
          </div>
        )}
      </section>
    </div>
  );
};

export default QuantumTab;
