import React, { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft, FileText, Lock, LockOpen, Mail, Plus, Scale } from "lucide-react";

import ClaimStatusPill from "@/components/claims/ClaimStatusPill";
import DelayTab from "@/components/claim-bench/DelayTab";
import DeterminationsPanel from "@/components/claim-bench/DeterminationsPanel";
import InternalAnalysisPanel from "@/components/claim-bench/InternalAnalysisPanel";
import SubmissionPanel from "@/components/claim-bench/SubmissionPanel";
import HeadPanel from "@/components/claim-bench/HeadPanel";
import QuantumTab from "@/components/claim-bench/QuantumTab";
import NoticeClockCard from "@/components/claim-bench/NoticeClockCard";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useClaimBench } from "@/hooks/useClaimBench";
import useHasPermission from "@/hooks/useHasPermission";
import { errorMessage } from "@/hooks/useLetterBench";
import {
  ELEMENT_LABEL,
  KIND_LABEL,
  PERSPECTIVE_LABEL,
  headProgress,
  lightDescription,
  lightTone,
  type LightTone,
} from "@/lib/claim-bench-logic";
import { formatCrore, formatDateIN } from "@/lib/format-in";
import { ELEMENT_KEYS, type ClaimHead, type Perspective } from "@/types/claimBench";

const LIGHT_CLASS: Record<LightTone, string> = {
  supported: "bg-green-700 border-green-700",
  weak: "bg-amber-600 border-amber-600",
  missing: "bg-red-700 border-red-700",
  na: "bg-slate-500 border-slate-500",
  // Proposed, not decided: an outline only, so it never reads as settled.
  proposed: "bg-transparent border-slate-500 border-dashed",
  empty: "bg-transparent border-slate-300",
};

const Lights: React.FC<{ head: ClaimHead }> = ({ head }) => (
  <ul className="flex gap-1.5" aria-label="Elements">
    {ELEMENT_KEYS.map((key) => (
      <li key={key}>
        <span
          role="img"
          aria-label={lightDescription(key, head.elements[key])}
          title={lightDescription(key, head.elements[key])}
          className={`flex h-5 w-5 items-center justify-center rounded-full border-2 text-[10px] font-semibold ${
            LIGHT_CLASS[lightTone(head.elements[key])]
          } ${head.elements[key]?.state ? "text-white" : "text-slate-600"}`}
        >
          {ELEMENT_LABEL[key][0]}
        </span>
      </li>
    ))}
  </ul>
);

type WorkspaceView = "board" | "determinations" | "submission" | "internal";

/**
 * The Claim Bench: a claim worked as a board of heads. Records decide the
 * facts, the claims skill (once published) proposes a view, and the person
 * makes every call. Nothing on this page decides for them.
 */
const ClaimBenchPage: React.FC = () => {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { query, open, addHead, patchHead, removeHead, decide, test, lock, unlock, perspective } = useClaimBench(id);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState<"elements" | "delay" | "quantum">("elements");
  const [chosenView, setChosenView] = useState<WorkspaceView | null>(null);
  // Hides the tab only. The server refuses privileged material to anyone without the permission.
  const mayReadInternal = useHasPermission("dms.claim.strategy.read");
  const data = query.data;

  if (query.isLoading) {
    return (
      <div className="container mx-auto space-y-4 p-6">
        <Skeleton className="h-10 w-1/2" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-96 w-full" />
      </div>
    );
  }
  if (query.isError || !data) {
    return (
      <div role="alert" className="container mx-auto max-w-xl space-y-3 p-8 text-center">
        <h1 className="text-lg font-semibold">This claim could not be opened</h1>
        <p className="text-sm text-muted-foreground">
          {errorMessage(query.error, "Check that the claim exists and that you have access to it.")}
        </p>
        <Button variant="outline" onClick={() => navigate("/claims")}>
          Back to the register
        </Button>
      </div>
    );
  }

  const { bench, gate } = data;
  const head = bench.heads.find((item) => item.head_id === selectedId) ?? bench.heads[0] ?? null;
  const busy =
    addHead.isPending || patchHead.isPending || removeHead.isPending || decide.isPending || lock.isPending || unlock.isPending;
  const locked = bench.stage === "locked";
  const readOnly = data.read_only || locked;
  const cannotTestReason = !data.perspective
    ? "State which party this project acts for first"
    : undefined;
  // An Engineer's or Employer's workspace opens on evaluation; a Contractor's on the board.
  const evaluating = data.perspective === "engineer" || data.perspective === "employer";
  const views: [WorkspaceView, string][] = [
    ["board", "Board"],
    ["determinations", evaluating ? "Determination" : "Determinations received"],
    ...(evaluating ? [] : ([["submission", "Submission"]] as [WorkspaceView, string][])),
    ...(mayReadInternal ? ([["internal", "Internal analysis"]] as [WorkspaceView, string][]) : []),
  ];
  const wanted = chosenView ?? (evaluating ? "determinations" : "board");
  const view = views.some(([key]) => key === wanted) ? wanted : "board";

  return (
    <div className="container mx-auto space-y-4 p-6">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <Button asChild variant="ghost" size="sm" className="-ml-2">
            <Link to="/claims">
              <ArrowLeft className="mr-2 h-4 w-4" aria-hidden="true" />
              Claim register
            </Link>
          </Button>
          <h1 className="flex flex-wrap items-center gap-2 text-2xl font-bold">
            {data.title}
            <ClaimStatusPill status={data.status} />
          </h1>
          <p className="text-sm text-muted-foreground">
            {data.claim_ref ? <span className="font-mono">{data.claim_ref}</span> : "No reference"}
            {data.perspective && <> · Worked for the {PERSPECTIVE_LABEL[data.perspective]}</>}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button asChild variant="outline" size="sm">
            <Link to={`/claims/${id}/record`}>
              <FileText className="mr-2 h-4 w-4" aria-hidden="true" />
              Documents, tasks and approval
            </Link>
          </Button>
          <Button asChild variant="outline" size="sm">
            <Link to={`/letters/bench/new?claimId=${encodeURIComponent(id)}`}>
              <Mail className="mr-2 h-4 w-4" aria-hidden="true" />
              Draft a notice letter
            </Link>
          </Button>
        </div>
      </header>

      {data.read_only && (
        <p role="status" className="rounded-md border border-slate-300 bg-slate-50 p-3 text-sm">
          {data.read_only_reason}
        </p>
      )}

      {!data.perspective && data.project_id && (
        <section
          aria-labelledby="perspective-heading"
          className="space-y-2 rounded-lg border border-amber-600 bg-amber-50 p-4 text-amber-950"
        >
          <h2 id="perspective-heading" className="text-sm font-semibold">
            Which party does this project act for?
          </h2>
          <p className="text-sm">
            A head is tested from one party's position, and that is never assumed. This is recorded once for the
            project and applies to all of its claims.
          </p>
          <div className="flex flex-wrap gap-2">
            {(Object.keys(PERSPECTIVE_LABEL) as Perspective[]).map((value) => (
              <Button
                key={value}
                size="sm"
                variant="outline"
                className="bg-white"
                disabled={perspective.isPending}
                onClick={() => perspective.mutate(value)}
              >
                {PERSPECTIVE_LABEL[value]}
              </Button>
            ))}
          </div>
        </section>
      )}

      <NoticeClockCard
        claimId={id}
        claimType={data.claim_type}
        projectId={data.project_id}
        assessment={data.notice_assessment}
        requirement={data.notice_requirement}
        readOnly={data.read_only}
      />

      {!data.exists ? (
        <section className="space-y-3 rounded-lg border bg-card p-8 text-center">
          <Scale className="mx-auto h-8 w-8 text-muted-foreground" aria-hidden="true" />
          <h2 className="text-lg font-semibold">Work this claim head by head</h2>
          <p className="mx-auto max-w-xl text-sm text-muted-foreground">
            Opening the board carries the claim's title, clauses, days and amount into a first head. Each head is then
            tested on event, notice, contractual basis, causation and quantum, and you make the call on each.
          </p>
          <Button disabled={open.isPending || data.read_only} onClick={() => open.mutate()}>
            Open the board
          </Button>
        </section>
      ) : (
        <>
        <div role="tablist" aria-label="Claim workspace" className="flex flex-wrap gap-1 rounded-lg border bg-card p-1">
          {views.map(([key, label]) => (
            <button
              key={key}
              type="button"
              role="tab"
              aria-selected={view === key}
              onClick={() => setChosenView(key)}
              className={`rounded-md px-4 py-1.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                view === key ? "bg-primary text-primary-foreground" : "hover:bg-muted"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
        {view === "determinations" && <DeterminationsPanel claimId={id} readOnly={data.read_only} />}
        {view === "submission" && <SubmissionPanel claimId={id} readOnly={data.read_only} />}
        {view === "internal" && <InternalAnalysisPanel claimId={id} readOnly={data.read_only} />}
        {view === "board" && (
        <div className="grid gap-4 lg:grid-cols-[260px_minmax(0,1fr)_280px]">
          <nav aria-label="Heads of claim" className="space-y-2">
            {bench.heads.map((item) => {
              const progress = headProgress(item);
              const active = item.head_id === head?.head_id;
              return (
                <button
                  key={item.head_id}
                  type="button"
                  aria-current={active ? "true" : undefined}
                  onClick={() => setSelectedId(item.head_id)}
                  className={`w-full space-y-2 rounded-lg border p-3 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                    active ? "border-primary bg-primary/5" : "bg-card hover:bg-muted/50"
                  }`}
                >
                  <div className="flex items-start justify-between gap-2">
                    <p className="text-sm font-medium">{item.title}</p>
                    <span className="shrink-0 font-mono text-xs text-muted-foreground">{item.head_id}</span>
                  </div>
                  <Lights head={item} />
                  <p className="text-xs text-muted-foreground">
                    {KIND_LABEL[item.kind]}
                    {item.eot_days_claimed != null && ` · ${item.eot_days_claimed} days`}
                    {item.amount_claimed != null && ` · ${formatCrore(item.amount_claimed)}`}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {progress.done ? "Decided" : `${progress.decided} of ${progress.total} decided`}
                    {item.test_stale && <span className="ml-1 font-medium text-amber-800">· test out of date</span>}
                  </p>
                </button>
              );
            })}
            <Button
              variant="outline"
              size="sm"
              className="w-full"
              disabled={readOnly || busy}
              onClick={() =>
                addHead.mutate(
                  { title: "New head of claim", kind: "time_and_cost", clause_refs: [] },
                  { onSuccess: (response) => setSelectedId(response.bench.heads[response.bench.heads.length - 1]?.head_id ?? null) }
                )
              }
            >
              <Plus className="mr-2 h-4 w-4" aria-hidden="true" />
              Add a head
            </Button>
          </nav>

          <main className="space-y-4">
            {head && (
              <div role="tablist" aria-label={`Parts of head ${head.head_id}`} className="flex gap-1 rounded-lg border bg-card p-1">
                {(
                  [
                    ["elements", "Elements"],
                    ["delay", "Delay"],
                    ["quantum", "Quantum"],
                  ] as const
                ).map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    role="tab"
                    aria-selected={tab === key}
                    onClick={() => setTab(key)}
                    className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring ${
                      tab === key ? "bg-primary text-primary-foreground" : "hover:bg-muted"
                    }`}
                  >
                    {label}
                  </button>
                ))}
              </div>
            )}
            {head && tab === "delay" ? (
              <DelayTab key={head.head_id} claimId={id} headId={head.head_id} boardVersion={bench.board_version} readOnly={readOnly} />
            ) : head && tab === "quantum" ? (
              <QuantumTab key={head.head_id} claimId={id} headId={head.head_id} boardVersion={bench.board_version} readOnly={readOnly} />
            ) : head ? (
              <HeadPanel
                head={head}
                readOnly={readOnly}
                busy={busy}
                testing={test.isPending}
                canTest={Boolean(data.perspective)}
                cannotTestReason={cannotTestReason}
                onPatch={(patch) => patchHead.mutate({ headId: head.head_id, patch })}
                onDecide={(element, decision) => decide.mutate({ headId: head.head_id, element, ...decision })}
                onTest={(force) => test.mutate({ headId: head.head_id, force })}
                onRemove={() => {
                  setSelectedId(null);
                  removeHead.mutate(head.head_id);
                }}
              />
            ) : (
              <p className="rounded-lg border bg-card p-6 text-sm text-muted-foreground">
                This claim has no heads. Add one to start.
              </p>
            )}
          </main>

          <aside className="space-y-4">
            <section aria-labelledby="bench-gate-heading" className="space-y-3 rounded-lg border bg-card p-4">
              <h2 id="bench-gate-heading" className="text-sm font-semibold">
                {locked ? "Board locked" : "Before the board can be locked"}
              </h2>
              {locked ? (
                <p className="text-sm text-muted-foreground">
                  Every head is decided. Reopen the board to change anything.
                </p>
              ) : gate.blockers.length > 0 ? (
                <ul className="list-disc space-y-1 pl-5 text-sm">
                  {gate.blockers.map((blocker) => (
                    <li key={blocker}>{blocker}</li>
                  ))}
                </ul>
              ) : (
                <p className="text-sm text-muted-foreground">Every element and head has your call.</p>
              )}
              {gate.stale_tests > 0 && !locked && (
                <p className="text-xs text-amber-800">
                  {gate.stale_tests} head{gate.stale_tests === 1 ? " was" : "s were"} edited after being tested. The
                  proposals shown describe the earlier version.
                </p>
              )}
              {locked ? (
                <Button variant="outline" size="sm" className="w-full" disabled={data.read_only || busy} onClick={() => unlock.mutate()}>
                  <LockOpen className="mr-2 h-4 w-4" aria-hidden="true" />
                  Reopen the board
                </Button>
              ) : (
                <Button size="sm" className="w-full" disabled={data.read_only || busy || !gate.can_lock} onClick={() => lock.mutate()}>
                  <Lock className="mr-2 h-4 w-4" aria-hidden="true" />
                  Lock the board
                </Button>
              )}
            </section>

            <section aria-labelledby="bench-skill-heading" className="space-y-2 rounded-lg border bg-card p-4">
              <h2 id="bench-skill-heading" className="text-sm font-semibold">
                Claims skill
              </h2>
              {data.skill.available ? (
                <p className="text-sm">
                  <Badge variant="success">In use</Badge>{" "}
                  <span className="text-muted-foreground">version {data.skill.version}</span>
                </p>
              ) : (
                <>
                  <p className="text-sm">
                    <Badge variant="warning">Not in use</Badge>{" "}
                    <span className="text-muted-foreground">{data.skill.reason}</span>
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Testing a head checks it against the records only. It reports what is recorded and what is not; it
                    does not assess entitlement, and never proposes that an element is supported.
                  </p>
                </>
              )}
            </section>

            {data.steps.length > 0 && (
              <section aria-labelledby="bench-steps-heading" className="space-y-2 rounded-lg border bg-card p-4">
                <h2 id="bench-steps-heading" className="text-sm font-semibold">
                  Tests run
                </h2>
                <ul className="space-y-2 text-xs">
                  {[...data.steps].reverse().map((step) => (
                    <li key={step.step_id} className={`rounded-md border p-2 ${step.stale ? "opacity-60" : ""}`}>
                      <p className="font-medium">
                        {step.subject_id.split(":").pop()} ·{" "}
                        {step.skill_version ? `claims skill ${step.skill_version}` : "records only"}
                        {step.stale && " · superseded"}
                      </p>
                      <p className="text-muted-foreground">
                        {formatDateIN(step.completed_at)}
                        {step.role && ` · for the ${step.role}`}
                      </p>
                      {step.degraded && step.error && <p className="text-amber-800">{step.error}</p>}
                    </li>
                  ))}
                </ul>
              </section>
            )}
          </aside>
        </div>
        )}
        </>
      )}
    </div>
  );
};

export default ClaimBenchPage;
