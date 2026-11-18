import React, { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { CheckCircle2, FilePlus2, Mail, RotateCcw, Send, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import UserPicker from "@/components/pickers/UserPicker";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useDeterminations } from "@/hooks/useClaimFindings";
import { parseAmount } from "@/lib/claim-bench-logic";
import {
  PARTY_LABEL,
  RESULT_LABEL,
  STATUS_LABEL,
  daysOnRecord,
  deadlineState,
  findingProblem,
  informationInput,
  letterOptions,
  linesOf,
  type InformationRow,
} from "@/lib/claim-findings-logic";
import { formatDateIN, formatINR, fromDateInput, toDateInput } from "@/lib/format-in";
import { listEntityDocumentLinks } from "@/services/document-relationships-api";
import { enhancedApi } from "@/services/enhanced-api";
import type {
  DeterminationView,
  FindingResult,
  HeadFigures,
  HeadFinding,
  HeadFindingInput,
} from "@/types/claimFindings";

interface Props {
  claimId: string;
  readOnly: boolean;
}

const select = "h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50";
const RESULTS = Object.keys(RESULT_LABEL) as FindingResult[];
const STATUS_VARIANT = {
  draft: "neutral",
  in_review: "warning",
  returned: "danger",
  approved: "success",
  recorded: "primary",
} as const;

interface FindingDraft {
  result: FindingResult | "";
  days: string;
  amount: string;
  findings: string;
  risks: string;
  information: InformationRow[];
}

const EMPTY_ROW: InformationRow = { item: "", deadline: "", consequence: "" };

const today = () => new Date().toISOString().slice(0, 10);

const toDraft = (finding: HeadFinding): FindingDraft => ({
  result: finding.result ?? "",
  days: finding.days != null ? String(finding.days) : "",
  amount: finding.amount != null ? String(finding.amount) : "",
  findings: finding.findings,
  risks: finding.residual_risks.join("\n"),
  information: finding.information_required.map((item) => ({
    item: item.item,
    deadline: toDateInput(item.deadline),
    consequence: item.consequence ?? "",
  })),
});

const toInput = (draft: FindingDraft): HeadFindingInput | null => {
  const amount = draft.amount.trim() ? parseAmount(draft.amount) : null;
  const days = draft.days.trim() ? Number(draft.days) : null;
  if (amount !== null && typeof amount !== "number") return null;
  if (days !== null && (!Number.isInteger(days) || days < 0)) return null;
  return {
    result: draft.result || null,
    days,
    amount: amount as number | null,
    findings: draft.findings.trim(),
    residual_risks: linesOf(draft.risks),
    information_required: informationInput(draft.information),
  };
};

const FindingEditor: React.FC<{
  finding: HeadFinding;
  figures?: HeadFigures;
  editable: boolean;
  saving: boolean;
  onSave: (input: HeadFindingInput) => void;
}> = ({ finding, figures, editable, saving, onSave }) => {
  const [draft, setDraft] = useState<FindingDraft>(() => toDraft(finding));
  useEffect(() => setDraft(toDraft(finding)), [finding]);
  const input = toInput(draft);
  const problem = input ? findingProblem(input) : "Days must be a whole number and the amount a figure.";
  const set = <K extends keyof FindingDraft>(key: K, value: FindingDraft[K]) =>
    setDraft((current) => ({ ...current, [key]: value }));
  const id = `finding-${finding.head_id}`;
  const proposal = finding.proposal;

  return (
    <li className="space-y-3 rounded-lg border bg-card p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h4 className="text-sm font-semibold">
          <span className="mr-2 font-mono text-xs text-muted-foreground">{finding.head_id}</span>
          {finding.head_title}
        </h4>
        {finding.result ? (
          <Badge variant={finding.result === "full" ? "success" : finding.result === "none" ? "danger" : "warning"}>
            {RESULT_LABEL[finding.result]}
          </Badge>
        ) : (
          <Badge variant="neutral">No finding yet</Badge>
        )}
      </div>

      <p className="text-xs text-muted-foreground">
        On record: {daysOnRecord(figures)}
        {figures?.amount_claimed != null && ` · ${formatINR(figures.amount_claimed)} claimed`}
        {figures?.amount_supported != null && ` · ${formatINR(figures.amount_supported)} with proof linked`}
        {Boolean(figures?.amount_locked) && ` · ${formatINR(figures!.amount_locked!)} without proof`}
      </p>

      {proposal && editable && (
        <div className="space-y-1 rounded-md border border-dashed border-slate-400 p-3 text-sm">
          <p className="font-medium">
            Proposed {proposal.proposed_by === "skill" ? "by the claims skill" : "from the records only"}:{" "}
            {RESULT_LABEL[proposal.result]}
          </p>
          <p className="text-muted-foreground">{proposal.findings}</p>
          {proposal.information_required.length > 0 && (
            <ul className="list-disc pl-5 text-xs text-muted-foreground">
              {proposal.information_required.map((item) => (
                <li key={item}>{item}</li>
              ))}
            </ul>
          )}
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() =>
              setDraft((current) => ({
                ...current,
                result: proposal.result,
                findings: proposal.findings,
                risks: proposal.residual_risks.join("\n"),
                information: proposal.information_required.map((item) => ({ ...EMPTY_ROW, item })),
              }))
            }
          >
            Start from this proposal
          </Button>
          <p className="text-xs text-muted-foreground">A proposal is not a finding. Nothing is recorded until you save.</p>
        </div>
      )}

      {editable ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="space-y-1">
            <Label htmlFor={`${id}-result`}>Finding</Label>
            <select
              id={`${id}-result`}
              className={select}
              value={draft.result}
              onChange={(event) => set("result", event.target.value as FindingResult | "")}
            >
              <option value="">Not yet decided</option>
              {RESULTS.map((value) => (
                <option key={value} value={value}>
                  {RESULT_LABEL[value]}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${id}-days`}>Days</Label>
            <Input id={`${id}-days`} inputMode="numeric" value={draft.days} onChange={(e) => set("days", e.target.value)} />
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${id}-amount`}>Amount (INR)</Label>
            <Input id={`${id}-amount`} inputMode="decimal" value={draft.amount} onChange={(e) => set("amount", e.target.value)} />
          </div>
          <div className="space-y-1 sm:col-span-3">
            <Label htmlFor={`${id}-reasons`}>Reasons</Label>
            <Textarea id={`${id}-reasons`} rows={3} value={draft.findings} onChange={(e) => set("findings", e.target.value)} />
          </div>
          <fieldset className="space-y-2 sm:col-span-3">
            <legend className="text-sm font-medium">Information required</legend>
            {draft.information.map((row, index) => {
              const setRow = (patch: Partial<InformationRow>) =>
                set(
                  "information",
                  draft.information.map((entry, at) => (at === index ? { ...entry, ...patch } : entry))
                );
              const rowId = `${id}-info-${index}`;
              return (
                <div key={index} className="grid gap-2 rounded-md border p-2 sm:grid-cols-[2fr_1fr_2fr_auto]">
                  <div className="space-y-1">
                    <Label htmlFor={`${rowId}-item`}>Item {index + 1}</Label>
                    <Input id={`${rowId}-item`} value={row.item} onChange={(e) => setRow({ item: e.target.value })} />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`${rowId}-deadline`}>Deadline</Label>
                    <Input
                      id={`${rowId}-deadline`}
                      type="date"
                      value={row.deadline}
                      onChange={(e) => setRow({ deadline: e.target.value })}
                    />
                  </div>
                  <div className="space-y-1">
                    <Label htmlFor={`${rowId}-consequence`}>If not supplied</Label>
                    <Input
                      id={`${rowId}-consequence`}
                      value={row.consequence}
                      onChange={(e) => setRow({ consequence: e.target.value })}
                    />
                  </div>
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    className="self-end"
                    aria-label={`Remove item ${index + 1}`}
                    onClick={() => set("information", draft.information.filter((_, at) => at !== index))}
                  >
                    Remove
                  </Button>
                </div>
              );
            })}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => set("information", [...draft.information, { ...EMPTY_ROW }])}
            >
              Add an information item
            </Button>
          </fieldset>
          <div className="space-y-1 sm:col-span-3">
            <Label htmlFor={`${id}-risks`}>Residual risks, one per line</Label>
            <Textarea id={`${id}-risks`} rows={2} value={draft.risks} onChange={(e) => set("risks", e.target.value)} />
          </div>
          <div className="flex flex-wrap items-center gap-3 sm:col-span-3">
            <Button size="sm" disabled={saving || !input || Boolean(problem)} onClick={() => input && onSave(input)}>
              Save finding
            </Button>
            {problem && draft.result && <p className="text-xs text-amber-800">{problem}</p>}
          </div>
        </div>
      ) : (
        <div className="space-y-2 text-sm">
          {(finding.days != null || finding.amount != null) && (
            <p className="font-medium">
              {finding.days != null && `${finding.days} days`}
              {finding.days != null && finding.amount != null && " · "}
              {finding.amount != null && formatINR(finding.amount)}
            </p>
          )}
          <p className="whitespace-pre-wrap">{finding.findings || "No reasons recorded."}</p>
          {finding.information_required.length > 0 && (
            <div>
              <p className="text-xs font-medium">Information required</p>
              <ul className="list-disc pl-5 text-xs">
                {finding.information_required.map((item) => {
                  const state = deadlineState(item.deadline, today());
                  return (
                    <li key={item.item}>
                      {item.item}
                      {item.deadline ? ` — by ${formatDateIN(item.deadline)}` : " — no date set"}
                      {state === "overdue" && <span className="ml-1 font-medium text-red-700">(overdue)</span>}
                      {state === "due" && <span className="ml-1 font-medium text-amber-700">(due soon)</span>}
                      {item.consequence && <span className="text-muted-foreground"> — if not supplied: {item.consequence}</span>}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}
    </li>
  );
};

interface Person {
  id: string;
  name: string;
}

/**
 * Two Chairs, the official chair. A determination made here (Engineer or
 * Employer workspace, with authority, approved by a second person) or one
 * received from the other side and transcribed. Neither changes the claim.
 */
const DeterminationsPanel: React.FC<Props> = ({ claimId, readOnly }) => {
  const { query, create, setFinding, act, submit, reassign, revise } = useDeterminations(claimId);
  const [receiving, setReceiving] = useState(false);
  const [received, setReceived] = useState({ issued_by: "engineer", document: "", reference: "", date: "" });
  const [reviewer, setReviewer] = useState("");
  const [comment, setComment] = useState("");
  const documents = useQuery({
    queryKey: ["claim-bench", "claim-documents", claimId],
    queryFn: () => listEntityDocumentLinks("claim", claimId),
    enabled: receiving,
    retry: false,
  });
  const people = useQuery({
    queryKey: ["people"],
    queryFn: async (): Promise<Person[]> =>
      ((await enhancedApi.getUsers()) || [])
        .filter((user) => !user.disabled)
        .map((user) => ({
          id: String(user.id || user._id || ""),
          name: [user.first_name, user.last_name].filter(Boolean).join(" ").trim() || user.username || user.email || "Unnamed user",
        }))
        .filter((person) => person.id),
    retry: false,
  });
  const data = query.data;
  if (query.isLoading) return <p className="p-4 text-sm text-muted-foreground">Loading determinations…</p>;
  if (!data) return <p role="alert" className="p-4 text-sm text-destructive">Determinations could not be loaded.</p>;

  const busy =
    create.isPending || setFinding.isPending || act.isPending || submit.isPending || reassign.isPending || revise.isPending;
  const open = data.items.find((item) => item.determination.origin === "authored" && item.determination.status !== "approved");
  const otherSides = (["engineer", "employer"] as const).filter((party) => party !== data.perspective);
  const nameOf = (id?: string | null) => people.data?.find((person) => person.id === id)?.name || (id ? "another person" : "");

  const card = (view: DeterminationView) => {
    const item = view.determination;
    const figures = (headId: string) => view.figures.find((entry) => entry.head_id === headId);
    const letters = letterOptions(item, data.items.map((entry) => entry.determination));
    return (
      <article key={item._id} aria-label={`Determination revision ${item.revision}`} className="space-y-3 rounded-lg border p-4">
        <header className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h3 className="text-base font-semibold">
              {item.origin === "authored"
                ? `Determination by the ${PARTY_LABEL[item.issued_by]}`
                : `Determination received from the ${PARTY_LABEL[item.issued_by]}`}
              <span className="ml-2 text-sm font-normal text-muted-foreground">revision {item.revision}</span>
            </h3>
            <p className="text-xs text-muted-foreground">
              {item.reference && `${item.reference} · `}
              {item.determination_date && `dated ${formatDateIN(item.determination_date)} · `}
              started {formatDateIN(item.created_at)}
              {item.status === "approved" && ` · approved by ${nameOf(item.decided_by)} on ${formatDateIN(item.decided_at)}`}
              {item.status === "in_review" && ` · with ${nameOf(item.reviewer_id)} for approval`}
            </p>
          </div>
          <Badge variant={STATUS_VARIANT[item.status]}>{STATUS_LABEL[item.status]}</Badge>
        </header>

        {view.board_changed && (
          <p role="status" className="rounded-md border border-amber-600 bg-amber-50 p-2 text-xs text-amber-950">
            The board has changed since these findings were started. Check each head against it.
          </p>
        )}
        {item.status === "returned" && item.history.length > 0 && (
          <p role="status" className="rounded-md border border-red-700 bg-red-50 p-2 text-xs text-red-950">
            Returned: {item.history[item.history.length - 1].comment}
          </p>
        )}

        <ul className="space-y-3">
          {item.heads.map((finding) => (
            <FindingEditor
              key={finding.head_id}
              finding={finding}
              figures={figures(finding.head_id)}
              editable={view.editable && !readOnly}
              saving={busy}
              onSave={(input) => setFinding.mutate({ id: item._id, headId: finding.head_id, version: item.version, finding: input })}
            />
          ))}
        </ul>

        {(letters.determination || letters.informationRequest) && !readOnly && (
          <div className="flex flex-wrap gap-2" aria-label="Letters from this determination">
            {letters.determination && (
              <Button asChild variant="outline" size="sm">
                <Link to={`/letters/bench/new?determinationId=${encodeURIComponent(item._id)}&purpose=determination`}>
                  <Mail className="mr-2 h-4 w-4" aria-hidden="true" />
                  Issue determination letter
                </Link>
              </Button>
            )}
            {letters.informationRequest && (
              <Button asChild variant="outline" size="sm">
                <Link to={`/letters/bench/new?determinationId=${encodeURIComponent(item._id)}&purpose=information_request`}>
                  <Mail className="mr-2 h-4 w-4" aria-hidden="true" />
                  Request information
                </Link>
              </Button>
            )}
            <p className="w-full text-xs text-muted-foreground">
              Opens the Letter Bench with the findings{letters.informationRequest ? ", the items asked for and their deadlines" : ""}.
              Save any change to the findings first: the letter takes them as they are now.
            </p>
          </div>
        )}

        {view.editable && !readOnly && (
          <footer className="space-y-3 border-t pt-3">
            {view.blockers.length > 0 && (
              <ul className="list-disc pl-5 text-xs text-muted-foreground">
                {view.blockers.map((blocker) => (
                  <li key={blocker}>{blocker}</li>
                ))}
              </ul>
            )}
            {item.origin === "authored" ? (
              <div className="flex flex-wrap items-end gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy}
                  onClick={() => act.mutate({ id: item._id, action: "propose", version: item.version })}
                >
                  <Sparkles className="mr-2 h-4 w-4" aria-hidden="true" />
                  Propose findings
                </Button>
                <div className="ml-auto flex items-end gap-2">
                  <div className="min-w-56 space-y-1">
                    <UserPicker
                      projectId={data.project_id ?? ""}
                      permission="dms.claim.determine"
                      label="Approver"
                      hint="People on this project who hold determination authority, other than whoever made the findings."
                      value={reviewer}
                      onChange={(userId) => setReviewer(userId)}
                      excludeIds={[
                        item.created_by ?? "",
                        ...item.heads.map((finding) => finding.decided_by ?? ""),
                      ].filter(Boolean)}
                    />
                  </div>
                  <Button
                    size="sm"
                    disabled={busy || view.blockers.length > 0 || !reviewer}
                    onClick={() => submit.mutate({ id: item._id, version: item.version, reviewerId: reviewer })}
                  >
                    <Send className="mr-2 h-4 w-4" aria-hidden="true" />
                    Submit for approval
                  </Button>
                </div>
              </div>
            ) : (
              <Button
                size="sm"
                disabled={busy || view.blockers.length > 0}
                onClick={() => act.mutate({ id: item._id, action: "record", version: item.version })}
              >
                <CheckCircle2 className="mr-2 h-4 w-4" aria-hidden="true" />
                Record as received
              </Button>
            )}
          </footer>
        )}

        {item.status === "in_review" && !view.may_decide && view.reviewer_eligible !== false && (
          <p className="border-t pt-3 text-xs text-muted-foreground">
            With {nameOf(item.reviewer_id) || "its reviewer"} for approval. Only they can approve it or send it back.
          </p>
        )}

        {item.status === "in_review" && view.reviewer_eligible === false && (
          <footer className="space-y-2 border-t pt-3">
            <p role="status" className="text-xs text-amber-700 dark:text-amber-400">
              {nameOf(item.reviewer_id) || "Its reviewer"} no longer holds determination authority on this project, so
              cannot approve it or send it back.
              {view.may_reassign ? " You can send it to another approver; the findings stay as they are." : " The person who sent it can send it to another approver."}
            </p>
            {view.may_reassign && !readOnly && (
              <div className="flex items-end gap-2">
                <div className="min-w-56 space-y-1">
                  <UserPicker
                    projectId={data.project_id ?? ""}
                    permission="dms.claim.determine"
                    label="New approver"
                    hint="People on this project who hold determination authority, other than whoever made the findings."
                    value={reviewer}
                    onChange={(userId) => setReviewer(userId)}
                    excludeIds={[
                      item.created_by ?? "",
                      item.submitted_by ?? "",
                      ...item.heads.map((finding) => finding.decided_by ?? ""),
                    ].filter(Boolean)}
                  />
                </div>
                <Button
                  size="sm"
                  disabled={busy || !reviewer}
                  onClick={() => reassign.mutate({ id: item._id, version: item.version, reviewerId: reviewer })}
                >
                  <Send className="mr-2 h-4 w-4" aria-hidden="true" />
                  Send to another approver
                </Button>
              </div>
            )}
          </footer>
        )}

        {item.status === "in_review" && view.may_decide && !readOnly && (
          <footer className="space-y-2 border-t pt-3">
            <Label htmlFor={`comment-${item._id}`}>Comment (needed to return it)</Label>
            <Textarea id={`comment-${item._id}`} rows={2} value={comment} onChange={(event) => setComment(event.target.value)} />
            <div className="flex flex-wrap gap-2">
              <Button
                size="sm"
                disabled={busy}
                onClick={() => act.mutate({ id: item._id, action: "approve", version: item.version, comment })}
              >
                <CheckCircle2 className="mr-2 h-4 w-4" aria-hidden="true" />
                Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={busy || !comment.trim()}
                onClick={() => act.mutate({ id: item._id, action: "return", version: item.version, comment })}
              >
                <RotateCcw className="mr-2 h-4 w-4" aria-hidden="true" />
                Return for changes
              </Button>
            </div>
            <p className="text-xs text-muted-foreground">
              A determination cannot be approved or returned by a person who made its findings.
            </p>
          </footer>
        )}

        {item.status === "approved" && item.origin === "authored" && data.can_author && !open && !readOnly && (
          <footer className="border-t pt-3">
            <Button variant="outline" size="sm" disabled={busy} onClick={() => revise.mutate(item._id)}>
              Start a new revision
            </Button>
            <p className="mt-1 text-xs text-muted-foreground">
              An approved determination is not changed. A revision is a new record that supersedes it.
            </p>
          </footer>
        )}
      </article>
    );
  };

  return (
    <section aria-labelledby="determinations-heading" className="space-y-4 rounded-lg border bg-card p-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 id="determinations-heading" className="text-lg font-semibold">
            Determinations
          </h2>
          <p className="text-sm text-muted-foreground">
            Official findings on this claim. They read the board and do not change it.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          {data.can_author && !open && (
            <Button size="sm" disabled={busy || readOnly} onClick={() => create.mutate({ origin: "authored" })}>
              <FilePlus2 className="mr-2 h-4 w-4" aria-hidden="true" />
              Start a determination
            </Button>
          )}
          <Button
            variant="outline"
            size="sm"
            disabled={busy || readOnly}
            onClick={() => {
              // Default to a party the project does not act for: its own determinations are authored, not received.
              setReceived((current) => ({
                ...current,
                issued_by: otherSides.includes(current.issued_by as "engineer" | "employer") ? current.issued_by : otherSides[0],
              }));
              setReceiving((value) => !value);
            }}
          >
            Record one received
          </Button>
        </div>
      </header>

      {!data.can_author && data.cannot_author_reason && (
        <p role="note" className="rounded-md border bg-muted/40 p-3 text-sm">
          {data.cannot_author_reason}
        </p>
      )}

      {receiving && (
        <form
          className="grid gap-3 rounded-lg border p-4 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate(
              {
                origin: "received",
                issued_by: received.issued_by as "engineer" | "employer",
                source_document_id: received.document,
                reference: received.reference.trim() || undefined,
                determination_date: fromDateInput(received.date) ?? null,
              },
              { onSuccess: () => setReceiving(false) }
            );
          }}
        >
          <div className="space-y-1">
            <Label htmlFor="received-from">Issued by</Label>
            <select
              id="received-from"
              className={select}
              value={received.issued_by}
              onChange={(event) => setReceived({ ...received, issued_by: event.target.value })}
            >
              {otherSides.map((party) => (
                <option key={party} value={party}>
                  {PARTY_LABEL[party]}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="received-document">The document it arrived in</Label>
            <select
              id="received-document"
              className={select}
              value={received.document}
              onChange={(event) => setReceived({ ...received, document: event.target.value })}
            >
              <option value="">Choose a document linked to this claim</option>
              {(documents.data ?? []).map((link) => (
                <option key={String(link.document_id)} value={String(link.document_id)}>
                  {link.document?.subject || link.document?.filename || String(link.document_id)}
                </option>
              ))}
            </select>
          </div>
          <div className="space-y-1">
            <Label htmlFor="received-reference">Its reference</Label>
            <Input id="received-reference" value={received.reference} onChange={(e) => setReceived({ ...received, reference: e.target.value })} />
          </div>
          <div className="space-y-1">
            <Label htmlFor="received-date">Its date</Label>
            <Input id="received-date" type="date" value={received.date} onChange={(e) => setReceived({ ...received, date: e.target.value })} />
          </div>
          <div className="sm:col-span-2">
            <Button type="submit" size="sm" disabled={busy || !received.document}>
              Start the transcription
            </Button>
            <p className="mt-1 text-xs text-muted-foreground">
              Link the determination letter to the claim first if it is not listed. The findings are then copied in
              head by head, and the record is closed.
            </p>
          </div>
        </form>
      )}

      {data.items.length === 0 ? (
        <p className="text-sm text-muted-foreground">No determination has been made or recorded for this claim.</p>
      ) : (
        <div className="space-y-4">{data.items.map(card)}</div>
      )}
    </section>
  );
};

export default DeterminationsPanel;
