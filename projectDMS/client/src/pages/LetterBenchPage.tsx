import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { isAxiosError } from "axios";
import { RefreshCw } from "lucide-react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import { AssertionCard } from "@/components/letter-bench/AssertionCard";
import { BenchHeader } from "@/components/letter-bench/BenchHeader";
import {
  BenchErrorBanner,
  InternalNotePanel,
  QuestionCard,
} from "@/components/letter-bench/BenchSidePanels";
import { IncomingLetterCanvas } from "@/components/letter-bench/IncomingLetterCanvas";
import { PositionGateBar } from "@/components/letter-bench/PositionGateBar";
import { ReplyComposer } from "@/components/letter-bench/ReplyComposer";
import { SkillPalette } from "@/components/letter-bench/SkillPalette";
import { SkillRail } from "@/components/letter-bench/SkillRail";
import { SkillStepDrawer } from "@/components/letter-bench/SkillStepDrawer";
import {
  BENCH_FULLSCREEN_MAX,
  BENCH_MIN_HEIGHT,
  BENCH_WIDE_MIN,
  decidedCount,
  fillHeight,
  isBenchBusy,
  moveChainLink,
  removeChainLink,
  reorderChain,
  stepsForLink,
  toChainInput,
} from "@/components/letter-bench/benchLogic";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { errorMessage, useBench, useBenchActions, useSkills } from "@/hooks/useLetterBench";
import { cn } from "@/lib/utils";
import { type BenchPane, useLetterBenchStore } from "@/stores/useLetterBenchStore";
import type { ChainLink, EffectiveSkill } from "@/types/letterBench";

const PANES: Array<{ id: BenchPane; label: string }> = [
  { id: "letter", label: "Letter" },
  { id: "positions", label: "Positions" },
  { id: "reply", label: "Reply" },
];

function isTypingTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  return target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(target.tagName);
}

/**
 * The Bench fits its working set in one viewport, whatever the app shell around
 * it does: it measures the space it actually has. The layout switches on the
 * width of its own container, not the window, because the shell's sidebar takes
 * a fixed share of a narrow screen.
 */
function useBenchFrame(ready: boolean) {
  const ref = useRef<HTMLDivElement>(null);
  const [frame, setFrame] = useState({ height: 720, wide: true, fullscreen: false });
  useLayoutEffect(() => {
    const element = ref.current;
    if (!element) return;
    const measure = () => {
      const fullscreen = window.innerWidth > 0 && window.innerWidth < BENCH_FULLSCREEN_MAX;
      const next = {
        fullscreen,
        height: fullscreen
          ? window.innerHeight
          : fillHeight(window.innerHeight, element.getBoundingClientRect().top + window.scrollY),
        wide: element.clientWidth === 0 || element.clientWidth >= BENCH_WIDE_MIN,
      };
      setFrame((current) =>
        current.height === next.height && current.wide === next.wide && current.fullscreen === next.fullscreen
          ? current
          : next
      );
    };
    measure();
    window.addEventListener("resize", measure);
    const observer = typeof ResizeObserver === "undefined" ? null : new ResizeObserver(measure);
    if (element.parentElement) observer?.observe(element.parentElement);
    return () => {
      window.removeEventListener("resize", measure);
      observer?.disconnect();
    };
  }, [ready]);
  return { ref, ...frame };
}

const BenchSkeleton = () => (
  <div className="space-y-3 p-4" aria-busy="true" aria-label="Loading the Letter Bench">
    <Skeleton className="h-12 w-full" />
    <Skeleton className="h-10 w-2/3" />
    <div className="grid gap-3 lg:grid-cols-2">
      <Skeleton className="h-[50vh]" />
      <Skeleton className="h-[50vh]" />
    </div>
  </div>
);

const LetterBenchPage: React.FC = () => {
  const { id = "" } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: bench, isLoading, error } = useBench(id);
  const actions = useBenchActions(id);
  const { data: skills = [] } = useSkills(id, { enabled: Boolean(bench) });

  const store = useLetterBenchStore();
  const reset = useLetterBenchStore((state) => state.reset);
  useEffect(() => reset, [id, reset]);

  const frame = useBenchFrame(Boolean(bench));
  const wide = frame.wide;
  const busy = isBenchBusy(bench);
  const readOnly = Boolean(bench?.read_only);
  const board = bench?.board;

  // "/" opens the skill palette for the selected assertion; Ctrl/Cmd+K does the same.
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (readOnly) return;
      const slash = event.key === "/" && !event.metaKey && !event.ctrlKey && !isTypingTarget(event.target);
      const chord = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k";
      if (slash || chord) {
        event.preventDefault();
        useLetterBenchStore.getState().openPalette();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [readOnly]);

  const applySkill = useCallback(
    async (skill: EffectiveSkill, assertionId: string | null) => {
      if (!board) return;
      try {
        if (assertionId) {
          await actions.patch.mutateAsync({
            assertionId,
            patch: { routed_skill_key: skill.skill_key },
          });
          await actions.runStep.mutateAsync({ skill_key: skill.skill_key, target_assertion_ids: [assertionId] });
        } else {
          const existing = board.chain.filter((link) => link.kind === "evaluation");
          if (!existing.some((link) => link.skill_key === skill.skill_key)) {
            await actions.setChain.mutateAsync([
              ...toChainInput(existing),
              { skill_key: skill.skill_key, target_assertion_ids: [] },
            ]);
          }
          await actions.runStep.mutateAsync({ skill_key: skill.skill_key });
        }
      } catch {
        // Each mutation reports its own failure.
      }
    },
    [actions.patch, actions.runStep, actions.setChain, board]
  );

  const openLink = useCallback(
    (link: ChainLink) => {
      if (!bench) return;
      const stepId = link.step_id ?? stepsForLink(link, bench.steps).at(-1)?.step_id;
      if (stepId) store.openStep(stepId);
      else if (link.reason) toast.message(link.label, { description: link.reason });
      else toast.message(link.label, { description: "This step has not produced anything yet." });
    },
    [bench, store]
  );

  const submitForReview = useCallback(async () => {
    if (!board?.draft_run_id) return;
    try {
      await actions.submit.mutateAsync(board.draft_run_id);
      toast.success("Submitted for review");
      navigate(`/letters/${id}/review`);
    } catch {
      // Reported by the mutation.
    }
  }, [actions.submit, board?.draft_run_id, id, navigate]);

  const orderedAssertions = useMemo(
    () => [...(board?.assertions ?? [])].sort((a, b) => a.order - b.order),
    [board?.assertions]
  );

  if (isLoading) return <BenchSkeleton />;

  if (error || !bench || !board) {
    const missing = isAxiosError(error) && error.response?.status === 404;
    return (
      <div className="mx-auto max-w-xl space-y-3 p-8 text-center">
        <h1 className="text-lg font-semibold">
          {missing ? "This letter is not on the Letter Bench" : "The Letter Bench could not be loaded"}
        </h1>
        <p className="text-sm text-muted-foreground">
          {missing
            ? "It was started in the earlier drafting screens, or the Bench is not switched on for this organisation."
            : errorMessage(error, "Try again in a moment.")}
        </p>
        <div className="flex justify-center gap-2">
          <Button asChild variant="outline">
            <Link to="/letters">Back to letters</Link>
          </Button>
          {missing && (
            <Button asChild>
              <Link to={`/letters/${id}/draft?classic=1`}>Open the earlier screens</Link>
            </Button>
          )}
        </div>
      </div>
    );
  }

  const pending =
    actions.lock.isPending || actions.compose.isPending || actions.submit.isPending || actions.dissect.isPending;
  const total = orderedAssertions.length;
  const dissecting = board.stage === "dissecting";
  const showReply = board.reply_paragraphs.length > 0 || board.composing || board.stage === "locked";

  return (
    <div
      ref={frame.ref}
      data-testid="letter-bench"
      data-layout={wide ? "wide" : "tabs"}
      style={frame.fullscreen ? undefined : { height: frame.height, minHeight: BENCH_MIN_HEIGHT }}
      className={cn(
        "flex flex-col overflow-hidden bg-background",
        // On a phone the Bench is the screen: the back arrow in its header leaves it.
        // Positioned, so visually-hidden (absolutely positioned) labels inside the
        // scrolling panes are clipped here instead of stretching the document.
        frame.fullscreen ? "fixed inset-0 z-40 h-[100dvh]" : "relative -m-6"
      )}
    >
      <BenchHeader
        bench={bench}
        onRole={(role) => actions.settings.mutate({ role })}
        onDeadline={(date, basis) => actions.settings.mutate({ deadline_date: date, deadline_basis: basis })}
      />
      <div className="border-b">
        <SkillRail
          chain={board.chain}
          readOnly={readOnly}
          onOpen={openLink}
          onAdd={() => store.openPalette()}
          onRemove={(link) => actions.setChain.mutate(removeChainLink(board.chain, link.link_id))}
          onMove={(link, direction) => actions.setChain.mutate(reorderChain(board.chain, link.link_id, direction))}
          onDrop={(fromId, toId) => actions.setChain.mutate(moveChainLink(board.chain, fromId, toId))}
          onRun={(link) =>
            actions.runStep.mutate({ skill_key: link.skill_key, target_assertion_ids: link.target_assertion_ids })
          }
        />
      </div>
      <BenchErrorBanner message={board.last_error} />

      {/* In a narrow container the three zones become tabs; the gate bar stays fixed. */}
      <div role="tablist" aria-label="Bench views" className={cn("border-b", wide ? "hidden" : "flex")}>
        {PANES.map((pane) => (
          <button
            key={pane.id}
            type="button"
            role="tab"
            id={`bench-tab-${pane.id}`}
            aria-selected={store.pane === pane.id}
            aria-controls={`bench-pane-${pane.id}`}
            onClick={() => store.setPane(pane.id)}
            className={cn(
              "min-h-11 flex-1 border-b-2 px-3 text-sm font-medium focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring",
              store.pane === pane.id
                ? "border-primary text-foreground"
                : "border-transparent text-muted-foreground"
            )}
          >
            {pane.label}
            {pane.id === "positions" && total > 0 && (
              <span className="ml-1.5 text-xs tabular-nums">
                {decidedCount(orderedAssertions)}/{total}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className={cn("grid min-h-0 flex-1", wide && "grid-cols-2")}>
        <section
          id="bench-pane-letter"
          aria-labelledby="bench-letter-heading"
          className={cn("min-h-0 flex-col border-r", wide || store.pane === "letter" ? "flex" : "hidden")}
        >
          <div className="flex items-center justify-between border-b px-4 py-2">
            <h2 id="bench-letter-heading" className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
              {board.trigger ? "Record" : "Incoming letter"}
            </h2>
            {!readOnly && (
              <Button
                variant="ghost"
                size="sm"
                className="h-7 text-xs"
                disabled={busy || actions.dissect.isPending}
                onClick={() => actions.dissect.mutate()}
              >
                <RefreshCw className={cn("mr-1 h-3 w-3", dissecting && "animate-spin motion-reduce:animate-none")} aria-hidden="true" />
                {dissecting ? "Analysing…" : "Analyse again"}
              </Button>
            )}
          </div>
          <IncomingLetterCanvas
            text={board.incoming_text}
            assertions={orderedAssertions}
            readOnly={readOnly}
            busy={dissecting}
            onCreate={(span) => actions.addAssertion.mutate(span)}
            onSplit={(assertionId, splitAt) =>
              actions.patch.mutate({ assertionId, patch: { split_at: splitAt } })
            }
          />
        </section>

        <div className={cn("min-h-0 flex-col", !wide && store.pane === "letter" ? "hidden" : "flex")}>
          <section
            id="bench-pane-positions"
            aria-labelledby="bench-positions-heading"
            className={cn(
              "min-h-0 flex-1 flex-col",
              !wide && store.pane === "reply" ? "hidden" : "flex",
              wide && showReply && "max-h-[55%]"
            )}
          >
            <div className="flex items-center justify-between border-b px-4 py-2">
              <h2 id="bench-positions-heading" className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                Positions
              </h2>
              <span className="text-xs tabular-nums text-muted-foreground">
                {decidedCount(orderedAssertions)} of {total} set
              </span>
            </div>
            <div data-bench-scroll="" className="relative min-h-0 flex-1 space-y-3 overflow-y-auto p-4">
              {dissecting && total === 0 && (
                <div className="space-y-3" aria-busy="true">
                  <p className="text-sm text-muted-foreground">Reading the letter and finding each assertion…</p>
                  <Skeleton className="h-28 w-full" />
                  <Skeleton className="h-28 w-full" />
                </div>
              )}
              {!dissecting && total === 0 && (
                <p className="rounded-md border border-dashed p-4 text-sm text-muted-foreground">
                  No assertions were found. Select a passage in the letter and choose “Add as assertion”.
                </p>
              )}
              {orderedAssertions.map((assertion, index) => {
                const routedLink = board.chain.find(
                  (link) =>
                    link.kind === "evaluation" &&
                    link.skill_key === assertion.routed_skill_key &&
                    (link.target_assertion_ids.length === 0 ||
                      link.target_assertion_ids.includes(assertion.assertion_id))
                );
                return (
                  <AssertionCard
                    key={assertion.assertion_id}
                    assertion={assertion}
                    routedLink={routedLink}
                    steps={bench.steps}
                    nextAssertionId={orderedAssertions[index + 1]?.assertion_id}
                    selected={store.selectedAssertionId === assertion.assertion_id}
                    active={store.activeAssertionId === assertion.assertion_id}
                    readOnly={readOnly}
                    onSelect={() => store.selectAssertion(assertion.assertion_id)}
                    onHover={(active) => store.setActiveAssertion(active ? assertion.assertion_id : null)}
                    onVerdict={(verdict) =>
                      actions.patch.mutate({ assertionId: assertion.assertion_id, patch: { verdict } })
                    }
                    onDirection={(text) =>
                      actions.patch.mutate({ assertionId: assertion.assertion_id, patch: { user_direction: text } })
                    }
                    onRouteSkill={() => store.openPalette(assertion.assertion_id)}
                    onOpenStep={store.openStep}
                    onMergeNext={() => {
                      const next = orderedAssertions[index + 1];
                      if (next) {
                        actions.patch.mutate({
                          assertionId: assertion.assertion_id,
                          patch: { merge_with: next.assertion_id },
                        });
                      }
                    }}
                    onRemove={() =>
                      actions.patch.mutate({ assertionId: assertion.assertion_id, patch: { remove: true } })
                    }
                  />
                );
              })}
              {board.questions.map((item) => (
                <QuestionCard
                  key={item.question.question_id}
                  item={item}
                  readOnly={readOnly}
                  onAnswer={(text) => actions.answer.mutate({ questionId: item.question.question_id, text })}
                />
              ))}
            </div>
          </section>

          {showReply && (
            <section
              id="bench-pane-reply"
              aria-labelledby="bench-reply-heading"
              className={cn(
                "min-h-0 flex-1 flex-col border-t",
                wide || store.pane === "reply" ? "flex" : "hidden"
              )}
            >
              <div className="flex items-center justify-between border-b px-4 py-2">
                <h2 id="bench-reply-heading" className="text-[11px] font-semibold uppercase tracking-wider text-muted-foreground">
                  Reply
                </h2>
                {board.draft_run_id && (
                  <Link
                    to={`/letters/${id}/draft?classic=1`}
                    className="text-xs font-medium text-primary underline-offset-2 hover:underline"
                  >
                    Checks, sources and export
                  </Link>
                )}
              </div>
              <div data-bench-scroll="" className="relative min-h-0 flex-1 overflow-y-auto">
                <ReplyComposer
                  paragraphs={board.reply_paragraphs}
                  assertions={orderedAssertions}
                  readOnly={readOnly}
                  composing={board.composing}
                  onSave={(paragraphId, text) => actions.editParagraph.mutate({ paragraphId, text })}
                  onRerun={(assertionId) =>
                    actions.runStep.mutate({
                      skill_key: "indian-infra-contract-letters",
                      target_assertion_ids: [assertionId],
                      force: true,
                    })
                  }
                />
                <InternalNotePanel note={board.internal_note} />
              </div>
            </section>
          )}
          {!showReply && !wide && store.pane === "reply" && (
            <p id="bench-pane-reply" className="p-6 text-center text-sm text-muted-foreground">
              The reply appears here once positions are locked.
            </p>
          )}
        </div>
      </div>

      <PositionGateBar
        bench={bench}
        busy={busy}
        pending={pending}
        onLock={() => actions.lock.mutate()}
        onCompose={() => actions.compose.mutate()}
        onSubmit={submitForReview}
      />

      <SkillPalette
        open={store.paletteOpen}
        onOpenChange={(open) => (open ? store.openPalette() : store.closePalette())}
        skills={skills}
        role={board.role}
        targetAssertionId={store.paletteAssertionId}
        gate={bench.gate}
        onApplySkill={applySkill}
        onReanalyse={() => actions.dissect.mutate()}
        onLock={() => actions.lock.mutate()}
        onCompose={() => actions.compose.mutate()}
      />
      <SkillStepDrawer
        letterId={id}
        stepId={store.openStepId}
        readOnly={readOnly}
        onClose={() => store.openStep(null)}
        onRerun={(step) => {
          actions.runStep.mutate({
            skill_key: step.skill_key,
            target_assertion_ids: step.target_assertion_ids,
            force: true,
          });
          store.openStep(null);
        }}
      />
    </div>
  );
};

export default LetterBenchPage;
