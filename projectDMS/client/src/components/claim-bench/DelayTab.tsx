import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Link2, RefreshCw, Unlink } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { headKeys, useHeadAnalysis } from "@/hooks/useClaimBench";
import OverlapCostBasis from "@/components/claim-bench/OverlapCostBasis";
import {
  CRITICALITY_LABEL,
  LANES,
  METHOD_LABEL,
  RISK_OWNER_LABEL,
  TREATMENT_HELP,
  TREATMENT_LABEL,
  UNAVAILABLE_METHOD_LABEL,
  buildTimeline,
  eventNeedsDecision,
  netEotExplanation,
  netEotText,
  overlapNames,
} from "@/lib/delay-lab-logic";
import { formatDateIN, fromDateInput, toDateInput } from "@/lib/format-in";
import { fetchDelayCandidates } from "@/services/claim-bench-api";
import type {
  Criticality,
  DelayMethod,
  DelayView,
  LinkCandidate,
  OverlapTreatment,
  OverlapView,
  RiskOwner,
} from "@/types/claimBench";

interface Props {
  claimId: string;
  headId: string;
  boardVersion: number;
  readOnly: boolean;
}

// Lane position and the label carry the risk owner; colour only reinforces it.
const LANE_BAR: Record<string, string> = {
  employer: "bg-sky-700",
  contractor: "bg-orange-700",
  neutral: "bg-slate-500",
  undecided: "bg-white border-2 border-dashed border-slate-500",
};

/** Label colour on each bar: white on the filled lanes, dark on the white "Not decided" one. */
const LANE_TEXT: Record<string, string> = {
  employer: "text-white",
  contractor: "text-white",
  neutral: "text-white",
  undecided: "text-slate-800",
};

const select = "h-9 rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50";

const Timeline: React.FC<{ view: DelayView }> = ({ view }) => {
  const timeline = buildTimeline(view);
  if (!timeline) {
    return (
      <p className="text-sm text-muted-foreground">
        Nothing to draw yet. Link a hindrance; an event with no end date also needs the date the analysis runs to.
      </p>
    );
  }
  const lanes = LANES.filter((lane) => lane.key !== "undecided" || timeline.bars.some((bar) => bar.lane === "undecided"));
  return (
    <figure aria-label="Delay events by risk owner" className="space-y-1">
      <div className="relative">
        {lanes.map((lane) => (
          <div key={lane.key} className="flex items-center gap-2 border-b py-1.5 last:border-b-0">
            <span className="w-24 shrink-0 text-xs font-medium text-muted-foreground">{lane.label}</span>
            <div className="relative h-6 flex-1 rounded bg-muted/40">
              {timeline.bars
                .filter((bar) => bar.lane === lane.key)
                .map((bar) => (
                  <span
                    key={bar.id}
                    title={`${bar.label}${bar.counted ? "" : " (not counted)"}${bar.openEnded ? ", runs to the analysis date" : ""}`}
                    className={`absolute top-0.5 flex h-5 items-center overflow-hidden rounded-sm ${LANE_BAR[bar.lane]} ${bar.counted ? "" : "opacity-50"}`}
                    style={{ left: `${bar.left}%`, width: `max(${bar.width}%, 4px)` }}
                  >
                    {/* The label is on the bar itself, not only in a tooltip (4B-25). */}
                    <span className={`truncate whitespace-nowrap px-1 text-[10px] font-medium ${LANE_TEXT[bar.lane]}`}>
                      {bar.label}
                      {bar.counted ? "" : " (not counted)"}
                    </span>
                  </span>
                ))}
            </div>
          </div>
        ))}
        {timeline.milestones.length > 0 && (
          <div className="flex items-center gap-2 border-t py-1.5">
            <span className="w-24 shrink-0 text-xs font-medium text-muted-foreground">Milestones</span>
            <ol aria-label="EOT submissions and determinations" className="relative h-6 flex-1 rounded bg-muted/40">
              {timeline.milestones.map((milestone) => (
                <li
                  key={milestone.id}
                  className="absolute top-0 flex h-6 -translate-x-1/2 items-center gap-1"
                  style={{ left: `${milestone.left}%` }}
                >
                  <span aria-hidden="true" className="h-2.5 w-2.5 rotate-45 bg-slate-700" />
                  <span className="whitespace-nowrap text-[10px] font-medium text-slate-800">
                    {milestone.label} · {formatDateIN(milestone.date)}
                    {milestone.granted != null ? ` · ${milestone.granted} days granted` : ""}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}
        {/* Overlaps span every lane: hatched while undecided, outlined once decided. */}
        <div className="pointer-events-none absolute inset-y-0 left-[6.5rem] right-0">
          {timeline.overlaps.map((overlap, index) => (
            <span
              key={overlap.id}
              className={`absolute inset-y-0 border-x-2 ${overlap.decided ? "border-slate-700" : "border-red-700"}`}
              style={{
                left: `${overlap.left}%`,
                width: `max(${overlap.width}%, 4px)`,
                backgroundImage: overlap.decided
                  ? undefined
                  : "repeating-linear-gradient(45deg, rgba(185,28,28,0.18) 0 4px, transparent 4px 8px)",
              }}
            >
              <span className="absolute -top-2 left-0 rounded bg-white px-1 text-[10px] font-semibold text-slate-800 shadow">
                O{index + 1}
              </span>
            </span>
          ))}
        </div>
      </div>
      <figcaption className="flex justify-between pl-[6.5rem] text-xs text-muted-foreground">
        <span>{formatDateIN(timeline.start)}</span>
        <span>{timeline.totalDays} days</span>
        <span>{formatDateIN(timeline.end)}</span>
      </figcaption>
    </figure>
  );
};

/** How the extension is made up (4B-25). The rows add up to the figure above, exactly. */
const NetEotTable: React.FC<{ view: DelayView }> = ({ view }) => {
  const rows = view.net_table ?? [];
  if (rows.length === 0) return null;
  const names = overlapNames(view);
  const ranged = rows.some((row) => row.up_to > 0);
  const label = (row: (typeof rows)[number]) => {
    if (!row.overlap_id) return row.label;
    const treatment = row.treatment ? TREATMENT_LABEL[row.treatment as OverlapTreatment] ?? row.treatment : "";
    return `${names[row.overlap_id] ?? "Overlap"}: ${treatment || row.label}`;
  };
  const signed = (value: number) => (value < 0 ? `−${Math.abs(value)}` : String(value));
  return (
    <table className="w-full text-sm" aria-label="How the extension is made up">
      <thead>
        <tr className="border-b text-left text-xs text-muted-foreground">
          <th scope="col" className="py-1 font-medium">
            Component
          </th>
          <th scope="col" className="py-1 text-right font-medium">
            Days
          </th>
          {ranged && (
            <th scope="col" className="py-1 text-right font-medium">
              Up to
            </th>
          )}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, index) => (
          <tr key={`${row.key}-${row.overlap_id ?? index}`} className="border-b last:border-b-0">
            <th scope="row" className="py-1 text-left font-normal">
              {label(row)}
            </th>
            <td className="py-1 text-right tabular-nums">{signed(row.days)}</td>
            {ranged && <td className="py-1 text-right tabular-nums">{row.up_to > 0 ? `+${row.up_to}` : ""}</td>}
          </tr>
        ))}
      </tbody>
      <tfoot>
        <tr className="border-t font-semibold">
          <th scope="row" className="py-1 text-left">
            Extension of time
          </th>
          <td className="py-1 text-right tabular-nums">{view.net.low}</td>
          {ranged && <td className="py-1 text-right tabular-nums">{view.net.high}</td>}
        </tr>
      </tfoot>
    </table>
  );
};

const COST_CARRYING: ReadonlySet<string> = new Set(["eot_granted_with_cost", "not_true_concurrency"]);

const OverlapCard: React.FC<{
  claimId: string;
  overlap: OverlapView;
  index: number;
  disabled: boolean;
  onDecide: (decision: { treatment?: string; days_to_eot?: number; basis?: string; clear?: boolean }) => void;
  onRecordCost: (cost: { contractual_basis: string; causation: string; evidence_document_ids: string[] }) => void;
  onReviewCost: () => void;
}> = ({ claimId, overlap, index, disabled, onDecide, onRecordCost, onReviewCost }) => {
  const neutral = overlap.kind === "neutral_contractor";
  const [treatment, setTreatment] = useState<OverlapTreatment | "">(overlap.finding?.treatment ?? "");
  const [basis, setBasis] = useState(overlap.finding?.basis ?? "");
  const [days, setDays] = useState(overlap.finding?.days_to_eot != null ? String(overlap.finding.days_to_eot) : "");
  const daysValue = Number(days);
  const daysOk = treatment !== "apportioned" || (/^\d+$/.test(days) && daysValue <= overlap.days);
  const ready = treatment !== "" && basis.trim().length >= 10 && daysOk;
  const id = `overlap-${overlap.overlap_id}`;
  return (
    <li className={`space-y-2 rounded-md border p-3 ${overlap.finding ? "" : "border-red-700 bg-red-50/50"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-sm font-medium">
          O{index + 1} · {formatDateIN(overlap.start_date)} to {formatDateIN(overlap.end_date)} · {overlap.days} days
        </p>
        {overlap.finding ? (
          <Badge variant="neutral">
            {TREATMENT_LABEL[overlap.finding.treatment]} · {overlap.days_to_eot} days to the extension
          </Badge>
        ) : (
          <Badge variant="danger">Treatment not decided</Badge>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {neutral ? (
          <>
            Neutral delay ({overlap.neutral_event_ids?.length ?? 0} event{overlap.neutral_event_ids?.length === 1 ? "" : "s"})
            and Contractor delay ({overlap.contractor_event_ids.length}) were both critical on these days. Decide whether the
            contract's neutral entitlement gives time for them. Neutral delay never carries cost.
          </>
        ) : (
          <>
            Employer delay ({overlap.employer_event_ids.length} event{overlap.employer_event_ids.length === 1 ? "" : "s"})
            and Contractor delay ({overlap.contractor_event_ids.length}) were both critical on these days. This is not
            deducted or added until you decide how the contract treats it.
          </>
        )}
      </p>
      <div className="grid gap-2 sm:grid-cols-[220px_1fr]">
        <div>
          <Label htmlFor={`${id}-treatment`} className="text-xs">
            Treatment
          </Label>
          <select
            id={`${id}-treatment`}
            className={`${select} w-full`}
            value={treatment}
            disabled={disabled}
            onChange={(e) => setTreatment(e.target.value as OverlapTreatment)}
          >
            <option value="">Choose…</option>
            {(Object.keys(TREATMENT_LABEL) as OverlapTreatment[]).map((key) => (
              <option key={key} value={key}>
                {TREATMENT_LABEL[key]}
              </option>
            ))}
          </select>
          {treatment === "apportioned" && (
            <div className="pt-2">
              <Label htmlFor={`${id}-days`} className="text-xs">
                Days counted (0 to {overlap.days})
              </Label>
              <Input id={`${id}-days`} inputMode="numeric" value={days} disabled={disabled} aria-invalid={!daysOk} onChange={(e) => setDays(e.target.value)} />
            </div>
          )}
        </div>
        <div>
          <Label htmlFor={`${id}-basis`} className="text-xs">
            Basis: the clause, or the method and reasoning relied on
          </Label>
          <Textarea id={`${id}-basis`} rows={2} value={basis} disabled={disabled} onChange={(e) => setBasis(e.target.value)} />
        </div>
      </div>
      {treatment !== "" && <p className="text-xs text-muted-foreground">{TREATMENT_HELP[treatment]}</p>}
      <div className="flex gap-2">
        <Button
          size="sm"
          disabled={disabled || !ready}
          onClick={() =>
            onDecide({
              treatment,
              basis: basis.trim(),
              days_to_eot: treatment === "apportioned" ? daysValue : undefined,
            })
          }
        >
          {overlap.finding ? "Update treatment" : "Record treatment"}
        </Button>
        {overlap.finding && (
          <Button size="sm" variant="ghost" disabled={disabled} onClick={() => onDecide({ clear: true })}>
            Clear
          </Button>
        )}
      </div>
      {!neutral && overlap.finding && COST_CARRYING.has(overlap.finding.treatment) && (
        <OverlapCostBasis
          claimId={claimId}
          overlap={overlap}
          disabled={disabled}
          onRecord={onRecordCost}
          onReview={onReviewCost}
        />
      )}
    </li>
  );
};

/**
 * The delay assessment of one head. Events come from linked hindrances, as they
 * were when linked. The figures count only what a person has decided, and an
 * overlap changes nothing until its treatment is recorded with a basis.
 */
const DelayTab: React.FC<Props> = ({ claimId, headId, boardVersion, readOnly }) => {
  const { delay, link, unlink, settings, decideEvent, decideOverlap, recordCostBasis, reviewCostBasis } = useHeadAnalysis(
    claimId,
    headId,
    boardVersion
  );
  const [picking, setPicking] = useState(false);
  const [milestones, setMilestones] = useState<Record<string, string>>({});
  const candidates = useQuery({
    queryKey: headKeys.candidates(claimId),
    queryFn: () => fetchDelayCandidates(claimId),
    enabled: picking,
  });
  const view = delay.data;
  if (delay.isLoading) return <p className="p-4 text-sm text-muted-foreground">Loading the delay assessment…</p>;
  if (!view) return <p role="alert" className="p-4 text-sm text-destructive">The delay assessment could not be loaded.</p>;

  const busy =
    link.isPending ||
    unlink.isPending ||
    settings.isPending ||
    decideEvent.isPending ||
    decideOverlap.isPending ||
    recordCostBasis.isPending ||
    reviewCostBasis.isPending;
  const disabled = readOnly || busy;
  const linked = new Set([...view.events.map((e) => `hindrance:${e.event_id}`), ...view.records.map((r) => `${r.target_type}:${r.target_id}`)]);
  const available = (candidates.data ?? []).filter((c) => !linked.has(`${c.target_type}:${c.target_id}`));
  const hasEvents = view.events.length > 0;

  const addCandidate = (candidate: LinkCandidate) =>
    link.mutate({
      target_type: candidate.target_type,
      target_id: candidate.target_id,
      milestone_ref: milestones[candidate.target_id] || undefined,
    });

  return (
    <div className="space-y-4">
      <section aria-labelledby="delay-net-heading" className="space-y-2 rounded-lg border bg-card p-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2 id="delay-net-heading" className="text-sm font-semibold">
            Extension of time on the decisions so far
          </h2>
          <p className="text-2xl font-bold tabular-nums">{hasEvents ? netEotText(view.net) : "—"}</p>
        </div>
        {hasEvents ? (
          <>
            <p className="text-sm text-muted-foreground">{netEotExplanation(view)}</p>
            {!view.net.settled && (
              <p className="text-sm font-medium text-red-800">
                Shown as a range because an overlap has no decided treatment. The lower figure counts none of its days,
                the higher all of them.
              </p>
            )}
            <NetEotTable view={view} />
            <dl className="grid grid-cols-2 gap-2 pt-1 text-xs sm:grid-cols-4">
              {[
                ["Employer critical days", view.employer_critical_days],
                ["Contractor critical days", view.contractor_critical_days],
                ["Contractor delay alone", view.contractor_only_days],
                ["Already granted", view.net.previously_granted],
              ].map(([label, value]) => (
                <div key={label as string} className="rounded-md bg-muted/50 p-2">
                  <dt className="text-muted-foreground">{label}</dt>
                  <dd className="text-base font-semibold tabular-nums">{value}</dd>
                </div>
              ))}
            </dl>
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            Link the hindrances this head relies on. Nothing in the hindrance register is changed by linking.
          </p>
        )}
        {view.blockers.length > 0 && (
          <ul className="list-disc pl-5 text-sm text-amber-900">
            {view.blockers.map((blocker) => (
              <li key={blocker}>{blocker.replace(/^H\d+:\s*/, "")}</li>
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="delay-method-heading" className="space-y-3 rounded-lg border bg-card p-4">
        <h2 id="delay-method-heading" className="text-sm font-semibold">
          Method
        </h2>
        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <Label htmlFor="delay-method" className="text-xs">
              Delay analysis method
            </Label>
            <select
              id="delay-method"
              className={`${select} w-full`}
              value={view.method ?? ""}
              disabled={disabled}
              onChange={(e) => e.target.value && settings.mutate({ method: e.target.value as DelayMethod })}
            >
              <option value="">Choose…</option>
              {(Object.keys(METHOD_LABEL) as DelayMethod[]).map((key) => (
                <option key={key} value={key}>
                  {METHOD_LABEL[key]}
                </option>
              ))}
            </select>
          </div>
          <div>
            <Label htmlFor="delay-as-of" className="text-xs">
              Analysis runs to (for events with no end date)
            </Label>
            <Input
              id="delay-as-of"
              type="date"
              value={toDateInput(view.as_of_date)}
              disabled={disabled}
              onChange={(e) => {
                const value = fromDateInput(e.target.value);
                settings.mutate(value ? { as_of_date: value } : { clear_as_of_date: true });
              }}
            />
          </div>
        </div>
        <p className="text-xs text-muted-foreground">
          Not available:{" "}
          {Object.entries(view.unavailable_methods)
            .map(([key, reason]) => `${UNAVAILABLE_METHOD_LABEL[key] ?? key} (${reason})`)
            .join(" ")}
        </p>
      </section>

      <section aria-labelledby="delay-timeline-heading" className="space-y-3 rounded-lg border bg-card p-4">
        <h2 id="delay-timeline-heading" className="text-sm font-semibold">
          Timeline
        </h2>
        <Timeline view={view} />
      </section>

      <section aria-labelledby="delay-events-heading" className="space-y-3 rounded-lg border bg-card p-4">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h2 id="delay-events-heading" className="text-sm font-semibold">
            Delay events ({view.events.length})
          </h2>
          <Button size="sm" variant="outline" disabled={readOnly} onClick={() => setPicking((open) => !open)}>
            <Link2 className="mr-2 h-4 w-4" aria-hidden="true" />
            {picking ? "Close" : "Link a record"}
          </Button>
        </div>

        {picking && (
          <div className="space-y-2 rounded-md border bg-muted/30 p-3">
            {candidates.isLoading && <p className="text-sm text-muted-foreground">Loading records…</p>}
            {candidates.data && available.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No further hindrances or EOT records of this project are available to you to link.
              </p>
            )}
            <ul className="max-h-72 space-y-1 overflow-y-auto">
              {available.map((candidate) => (
                <li key={`${candidate.target_type}-${candidate.target_id}`} className="flex flex-wrap items-center justify-between gap-2 rounded-md border bg-white p-2 text-sm">
                  <div className="min-w-0">
                    <p className="font-medium">{candidate.label}</p>
                    <p className="text-xs text-muted-foreground">
                      {candidate.title ? `${candidate.title} · ` : ""}
                      {formatDateIN(candidate.date)}
                      {candidate.status ? ` · ${candidate.status.replace(/_/g, " ")}` : ""}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    {candidate.milestones.length > 1 && (
                      <select
                        aria-label={`Milestone of ${candidate.label} this head concerns`}
                        className={select}
                        value={milestones[candidate.target_id] ?? ""}
                        onChange={(e) => setMilestones({ ...milestones, [candidate.target_id]: e.target.value })}
                      >
                        <option value="">Which milestone?</option>
                        {candidate.milestones.map((ref) => (
                          <option key={ref} value={ref}>
                            {ref}
                          </option>
                        ))}
                      </select>
                    )}
                    <Button size="sm" variant="outline" disabled={disabled} onClick={() => addCandidate(candidate)}>
                      Link
                    </Button>
                  </div>
                </li>
              ))}
            </ul>
          </div>
        )}

        <ul className="space-y-2">
          {view.events.map((event) => (
            <li key={event.event_id} className="space-y-2 rounded-md border p-3">
              <div className="flex flex-wrap items-start justify-between gap-2">
                <div>
                  <p className="text-sm font-medium">
                    {event.label} · {event.title}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {formatDateIN(event.start_date)} to {event.end_date ? formatDateIN(event.end_date) : "not ended"}
                    {event.days != null && ` · ${event.days} days`}
                    {!event.counted && !eventNeedsDecision(event) && " · not counted"}
                  </p>
                </div>
                <div className="flex gap-1">
                  {!event.revision_current && (
                    <Button
                      size="sm"
                      variant="outline"
                      disabled={disabled}
                      title="Replace the link with one to the hindrance as it is now. Your decision is kept."
                      onClick={() => unlink.mutate({ linkId: event.link_id, refresh: true })}
                    >
                      <RefreshCw className="mr-1 h-4 w-4" aria-hidden="true" />
                      Use latest
                    </Button>
                  )}
                  <Button size="sm" variant="ghost" disabled={disabled} onClick={() => unlink.mutate({ linkId: event.link_id })}>
                    <Unlink className="mr-1 h-4 w-4" aria-hidden="true" />
                    Unlink
                  </Button>
                </div>
              </div>
              {!event.revision_current && (
                <p className="flex items-center gap-1 text-xs font-medium text-amber-800">
                  <AlertTriangle className="h-3.5 w-3.5" aria-hidden="true" />
                  The hindrance has changed since it was linked. The assessment still uses the linked version.
                </p>
              )}
              <div className="grid gap-2 sm:grid-cols-2">
                <div>
                  <Label htmlFor={`owner-${event.event_id}`} className="text-xs">
                    Whose risk
                  </Label>
                  <select
                    id={`owner-${event.event_id}`}
                    className={`${select} w-full`}
                    value={event.decision.risk_owner ?? ""}
                    disabled={disabled}
                    onChange={(e) =>
                      decideEvent.mutate(
                        e.target.value
                          ? { eventId: event.event_id, risk_owner: e.target.value as RiskOwner }
                          : { eventId: event.event_id, clear_fields: ["risk_owner"] }
                      )
                    }
                  >
                    <option value="">Not decided</option>
                    {(Object.keys(RISK_OWNER_LABEL) as RiskOwner[]).map((key) => (
                      <option key={key} value={key}>
                        {RISK_OWNER_LABEL[key]}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <Label htmlFor={`critical-${event.event_id}`} className="text-xs">
                    Critical path
                  </Label>
                  <select
                    id={`critical-${event.event_id}`}
                    className={`${select} w-full`}
                    value={event.decision.criticality ?? ""}
                    disabled={disabled}
                    onChange={(e) =>
                      decideEvent.mutate(
                        e.target.value
                          ? { eventId: event.event_id, criticality: e.target.value as Criticality }
                          : { eventId: event.event_id, clear_fields: ["criticality"] }
                      )
                    }
                  >
                    <option value="">Not decided</option>
                    {(Object.keys(CRITICALITY_LABEL) as Criticality[]).map((key) => (
                      <option key={key} value={key}>
                        {CRITICALITY_LABEL[key]}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              {event.decision.proposed_reason && (
                <p className="rounded-md bg-muted/50 p-2 text-xs text-muted-foreground">
                  <span className="uppercase">
                    {event.decision.proposed_by === "skill" ? "Proposed by the EOT skill" : "From the hindrance register"}:{" "}
                  </span>
                  {event.decision.proposed_reason}
                </p>
              )}
            </li>
          ))}
        </ul>
      </section>

      {view.overlaps.length > 0 && (
        <section aria-labelledby="delay-overlaps-heading" className="space-y-3 rounded-lg border bg-card p-4">
          <h2 id="delay-overlaps-heading" className="text-sm font-semibold">
            Overlaps of concurrent delay ({view.overlaps.length})
          </h2>
          <ul className="space-y-2">
            {view.overlaps.map((overlap, index) => (
              <OverlapCard
                key={`${overlap.overlap_id}-${overlap.finding?.decided_at ?? "open"}`}
                overlap={overlap}
                index={index}
                disabled={disabled}
                claimId={claimId}
                onDecide={(decision) => decideOverlap.mutate({ overlapId: overlap.overlap_id, ...decision })}
                onRecordCost={(cost) => recordCostBasis.mutate({ overlapId: overlap.overlap_id, ...cost })}
                onReviewCost={() => reviewCostBasis.mutate({ overlapId: overlap.overlap_id })}
              />
            ))}
          </ul>
        </section>
      )}

      {view.records.length > 0 && (
        <section aria-labelledby="delay-records-heading" className="space-y-2 rounded-lg border bg-card p-4">
          <h2 id="delay-records-heading" className="text-sm font-semibold">
            Linked EOT records
          </h2>
          <ul className="space-y-2">
            {view.records.map((record) => (
              <li key={record.link_id} className="flex flex-wrap items-start justify-between gap-2 rounded-md border p-3 text-sm">
                <div>
                  <p className="font-medium">{record.label}</p>
                  <p className="text-xs text-muted-foreground">
                    {formatDateIN(record.date)}
                    {record.status ? ` · ${record.status.replace(/_/g, " ")}` : ""}
                    {record.milestone_ref ? ` · milestone ${record.milestone_ref}` : ""}
                  </p>
                  {record.target_type === "eot_determination" && (
                    <p className={`text-xs ${record.counted ? "text-green-800" : "text-amber-800"}`}>
                      {record.counted
                        ? `${record.granted_days} days counted as already granted`
                        : `Not counted as already granted. ${record.not_counted_reason ?? ""}`}
                    </p>
                  )}
                </div>
                <Button size="sm" variant="ghost" disabled={disabled} onClick={() => unlink.mutate({ linkId: record.link_id })}>
                  <Unlink className="mr-1 h-4 w-4" aria-hidden="true" />
                  Unlink
                </Button>
              </li>
            ))}
          </ul>
        </section>
      )}

      {view.compensable_periods.length > 0 && (
        <section aria-labelledby="delay-compensable-heading" className="space-y-1 rounded-lg border bg-card p-4">
          <h2 id="delay-compensable-heading" className="text-sm font-semibold">
            Periods decided as compensable
          </h2>
          <p className="text-xs text-muted-foreground">
            Prolongation cost in the quantum build-up counts only inside these periods.
          </p>
          <ul className="text-sm">
            {view.compensable_periods.map((period) => (
              <li key={period.start_date}>
                {formatDateIN(period.start_date)} to {formatDateIN(period.end_date)} · {period.days} days
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
};

export default DelayTab;
