import React, { useEffect, useState } from "react";
import { EyeOff, Sparkles } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useInternalAnalysis } from "@/hooks/useClaimFindings";
import { errorMessage } from "@/hooks/useLetterBench";
import { PARTY_LABEL, RESULT_LABEL, linesOf } from "@/lib/claim-findings-logic";
import { formatDateIN } from "@/lib/format-in";
import type { ClaimSimulation, StrategyNote } from "@/types/claimFindings";

interface Props {
  claimId: string;
  readOnly: boolean;
}

/** The label goes on every panel, so no part of this can be shown without it. */
const Marked: React.FC<{ label: string; heading: string; id: string; children: React.ReactNode }> = ({
  label,
  heading,
  id,
  children,
}) => (
  <section
    aria-labelledby={id}
    className="space-y-3 rounded-lg border-2 border-dashed border-violet-700 bg-violet-50 p-4 text-violet-950"
  >
    <header className="flex flex-wrap items-center justify-between gap-2">
      <h3 id={id} className="text-base font-semibold">
        {heading}
      </h3>
      <span className="inline-flex items-center gap-1 rounded-full bg-violet-900 px-3 py-1 text-xs font-semibold text-white">
        <EyeOff className="h-3.5 w-3.5" aria-hidden="true" />
        {label}
      </span>
    </header>
    {children}
  </section>
);

const Simulation: React.FC<{ simulation: ClaimSimulation; currentVersion?: number | null }> = ({
  simulation,
  currentVersion,
}) => (
  <Marked
    id={`simulation-${simulation._id}`}
    label={simulation.label}
    heading={`How the ${PARTY_LABEL[simulation.simulated_party]} is likely to respond`}
  >
    <p className="text-xs">
      Prepared for the {PARTY_LABEL[simulation.prepared_for]} on {formatDateIN(simulation.created_at)}.
      {currentVersion != null && simulation.board_version !== currentVersion && (
        <span className="font-medium"> The board has changed since.</span>
      )}
    </p>
    {simulation.degraded && (
      <p role="status" className="rounded-md border border-violet-700 bg-white p-2 text-xs">
        {simulation.degraded_reason}
      </p>
    )}
    <ul className="space-y-3">
      {simulation.heads.map((head) => (
        <li key={head.head_id} className="space-y-1 rounded-md border border-violet-300 bg-white p-3 text-sm">
          <p className="font-medium">
            <span className="mr-2 font-mono text-xs">{head.head_id}</span>
            {head.head_title}
            <Badge variant="neutral" className="ml-2">
              {head.produced_by === "skill" ? `Likely: ${RESULT_LABEL[head.likely_result]}` : "No response predicted"}
            </Badge>
          </p>
          <p>{head.reasoning}</p>
          {head.likely_challenges.length > 0 && (
            <>
              <p className="text-xs font-medium">Open to challenge</p>
              <ul className="list-disc pl-5 text-xs">
                {head.likely_challenges.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
          {head.information_likely_requested.length > 0 && (
            <>
              <p className="text-xs font-medium">Likely to be asked for</p>
              <ul className="list-disc pl-5 text-xs">
                {head.information_likely_requested.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}
        </li>
      ))}
    </ul>
  </Marked>
);

const NoteEditor: React.FC<{
  note: StrategyNote | null;
  label: string;
  readOnly: boolean;
  busy: boolean;
  onGenerate: () => void;
  onSave: (version: number, input: { strengths: string[]; weaknesses: string[]; residual_risks: string[]; notes: string }) => void;
}> = ({ note, label, readOnly, busy, onGenerate, onSave }) => {
  const [draft, setDraft] = useState({ strengths: "", weaknesses: "", risks: "", notes: "" });
  useEffect(() => {
    setDraft({
      strengths: (note?.strengths ?? []).join("\n"),
      weaknesses: (note?.weaknesses ?? []).join("\n"),
      risks: (note?.residual_risks ?? []).join("\n"),
      notes: note?.notes ?? "",
    });
  }, [note]);
  const field = (key: keyof typeof draft, text: string, rows: number) => (
    <div className="space-y-1">
      <Label htmlFor={`strategy-${key}`}>{text}</Label>
      <Textarea
        id={`strategy-${key}`}
        rows={rows}
        className="bg-white"
        disabled={readOnly}
        value={draft[key]}
        onChange={(event) => setDraft({ ...draft, [key]: event.target.value })}
      />
    </div>
  );
  return (
    <Marked id="strategy-note-heading" label={label} heading="Strategy note">
      <p className="text-xs">
        Where the claim is strong, where it is weak and what risk remains. It is never included in the submission, in
        any export or in an arbitration bundle.
        {note && ` Last changed ${formatDateIN(note.updated_at)}.`}
      </p>
      {note?.degraded && note.degraded_reason && (
        <p role="status" className="rounded-md border border-violet-700 bg-white p-2 text-xs">
          Drafted from the board's decisions only. {note.degraded_reason}
        </p>
      )}
      {field("strengths", "Strengths, one per line", 4)}
      {field("weaknesses", "Weaknesses, one per line", 4)}
      {field("risks", "Residual risks, one per line", 3)}
      {field("notes", "Notes", 4)}
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          disabled={readOnly || busy}
          onClick={() =>
            onSave(note?.version ?? 0, {
              strengths: linesOf(draft.strengths),
              weaknesses: linesOf(draft.weaknesses),
              residual_risks: linesOf(draft.risks),
              notes: draft.notes,
            })
          }
        >
          Save note
        </Button>
        <Button size="sm" variant="outline" className="bg-white" disabled={readOnly || busy} onClick={onGenerate}>
          <Sparkles className="mr-2 h-4 w-4" aria-hidden="true" />
          {note ? "Re-draft the lists from the board" : "Draft from the board"}
        </Button>
      </div>
      <p className="text-xs">Re-drafting replaces the three lists and keeps your notes.</p>
    </Marked>
  );
};

/**
 * Two Chairs, the other chair: a party's own analysis of the claim. Privileged.
 * Everything here is framed and labelled differently from the board and from a
 * determination, and none of it is a finding.
 */
const InternalAnalysisPanel: React.FC<Props> = ({ claimId, readOnly }) => {
  const { simulations, strategy, simulate, generate, save } = useInternalAnalysis(claimId);
  if (simulations.isLoading || strategy.isLoading) {
    return <p className="p-4 text-sm text-muted-foreground">Loading internal analysis…</p>;
  }
  if (simulations.isError || strategy.isError || !simulations.data || !strategy.data) {
    return (
      <p role="alert" className="rounded-lg border bg-card p-4 text-sm">
        {errorMessage(simulations.error ?? strategy.error, "Internal analysis could not be loaded.")}
      </p>
    );
  }
  const list = simulations.data;
  return (
    <div className="space-y-4">
      <section aria-labelledby="simulate-heading" className="space-y-3 rounded-lg border bg-card p-4">
        <h2 id="simulate-heading" className="text-lg font-semibold">
          Internal analysis
        </h2>
        <p className="text-sm text-muted-foreground">
          Your side's own view of the claim. A simulation models how another party is likely to respond; it is not a
          determination, changes nothing on the claim and is never exported.
        </p>
        <div className="flex flex-wrap gap-2">
          {list.can_simulate.map((party) => (
            <Button key={party} size="sm" disabled={readOnly || simulate.isPending} onClick={() => simulate.mutate(party)}>
              Simulate the {PARTY_LABEL[party]}'s response
            </Button>
          ))}
          {list.can_simulate.length === 0 && (
            <p className="text-sm">State which party this project acts for before simulating another party's response.</p>
          )}
        </div>
      </section>
      {list.items.map((item) => (
        <Simulation key={item._id} simulation={item} currentVersion={list.current_board_version} />
      ))}
      <NoteEditor
        note={strategy.data.note ?? null}
        label={strategy.data.label}
        readOnly={readOnly}
        busy={generate.isPending || save.isPending}
        onGenerate={() => generate.mutate()}
        onSave={(version, note) => save.mutate({ version, note })}
      />
    </div>
  );
};

export default InternalAnalysisPanel;
