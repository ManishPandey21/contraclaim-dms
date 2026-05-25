import { useCallback, useEffect, useState } from "react";
import { FileSearch, History, Link2, MessageSquare, RefreshCw, RotateCcw, Users } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useLetterDrafting } from "@/hooks/useLetterDrafting";
import type {
  DraftAuditResponse,
  DraftContextPack,
  DraftGovernanceResponse,
  DraftRunResponse,
  SourceLedgerResponse,
} from "@/types/letterDrafting";
import { formatDateTime } from "@/utils/dateFormat";

interface DraftEvidencePanelProps {
  letterId?: string;
  runId?: string;
  run?: DraftRunResponse | null;
}

export function DraftEvidencePanel({
  letterId,
  runId,
  run,
}: DraftEvidencePanelProps) {
  const {
    addComment,
    assignReviewer,
    getAudit,
    getContextPack,
    getGovernance,
    getSourceLedger,
    returnForCorrection,
  } = useLetterDrafting();
  const [audit, setAudit] = useState<DraftAuditResponse | null>(null);
  const [contextPack, setContextPack] = useState<DraftContextPack | null>(null);
  const [sourceLedger, setSourceLedger] = useState<SourceLedgerResponse | null>(
    null
  );
  const [governance, setGovernance] =
    useState<DraftGovernanceResponse | null>(null);
  const [reviewerId, setReviewerId] = useState("");
  const [reviewNote, setReviewNote] = useState("");
  const [commentBody, setCommentBody] = useState("");
  const [returnReason, setReturnReason] = useState("");
  const [requiredChanges, setRequiredChanges] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const canLoad = Boolean(letterId && runId);

  const loadEvidence = useCallback(
    async () => {
      if (!letterId || !runId) return;
      setLoading(true);
      setError(null);
      try {
        const [auditResponse, contextResponse, sourceResponse, governanceResponse] =
          await Promise.all([
            getAudit(letterId, runId),
            getContextPack(letterId, runId),
            getSourceLedger(letterId, runId),
            getGovernance(letterId, runId),
          ]);
        setAudit(auditResponse);
        setContextPack(contextResponse);
        setSourceLedger(sourceResponse);
        setGovernance(governanceResponse);
      } catch (err: any) {
        setError(err?.message ?? "Unable to load evidence package.");
      } finally {
        setLoading(false);
      }
    },
    [getAudit, getContextPack, getGovernance, getSourceLedger, letterId, runId]
  );

  const handleAssignReviewer = useCallback(async () => {
    if (!letterId || !runId || !reviewerId.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await assignReviewer(letterId, runId, {
        reviewer_user_id: reviewerId.trim(),
        note: reviewNote.trim() || undefined,
      });
      setGovernance(response);
      setReviewerId("");
      setReviewNote("");
    } catch (err: any) {
      setError(err?.message ?? "Unable to assign reviewer.");
    } finally {
      setSubmitting(false);
    }
  }, [assignReviewer, letterId, reviewNote, reviewerId, runId]);

  const handleAddComment = useCallback(async () => {
    if (!letterId || !runId || !commentBody.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const response = await addComment(letterId, runId, {
        body: commentBody.trim(),
        visibility: "reviewer",
      });
      setGovernance(response);
      setCommentBody("");
    } catch (err: any) {
      setError(err?.message ?? "Unable to add comment.");
    } finally {
      setSubmitting(false);
    }
  }, [addComment, commentBody, letterId, runId]);

  const handleReturnForCorrection = useCallback(async () => {
    if (!letterId || !runId || !returnReason.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      await returnForCorrection(letterId, runId, {
        reason: returnReason.trim(),
        required_changes: requiredChanges
          .split("\n")
          .map((item) => item.trim())
          .filter(Boolean),
      });
      setReturnReason("");
      setRequiredChanges("");
      await loadEvidence();
    } catch (err: any) {
      setError(err?.message ?? "Unable to return draft for correction.");
    } finally {
      setSubmitting(false);
    }
  }, [letterId, loadEvidence, requiredChanges, returnForCorrection, returnReason, runId]);

  useEffect(() => {
    if (!canLoad) return;
    void loadEvidence();
  }, [canLoad, loadEvidence]);

  return (
    <Card>
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle className="text-sm font-semibold">Evidence Package</CardTitle>
        <Button
          size="sm"
          variant="outline"
          className="gap-2"
          onClick={() => void loadEvidence()}
          disabled={!canLoad || loading}
        >
          <RefreshCw className={`h-4 w-4 ${loading ? "animate-spin" : ""}`} />
          Refresh
        </Button>
      </CardHeader>
      <CardContent>
        {!canLoad ? (
          <p className="text-sm text-muted-foreground">
            Generate a v2 draft run to inspect context, sources, and audit events.
          </p>
        ) : error ? (
          <p className="text-sm text-destructive">{error}</p>
        ) : (
          <Tabs defaultValue="sources" className="w-full">
            <TabsList className="grid w-full grid-cols-5">
              <TabsTrigger value="sources" className="gap-1">
                <Link2 className="h-3.5 w-3.5" />
                Sources
              </TabsTrigger>
              <TabsTrigger value="context" className="gap-1">
                <FileSearch className="h-3.5 w-3.5" />
                Context
              </TabsTrigger>
              <TabsTrigger value="audit" className="gap-1">
                <History className="h-3.5 w-3.5" />
                Audit
              </TabsTrigger>
              <TabsTrigger value="governance" className="gap-1">
                <Users className="h-3.5 w-3.5" />
                Governance
              </TabsTrigger>
              <TabsTrigger value="cyclic">Cycle</TabsTrigger>
            </TabsList>

            <TabsContent value="sources" className="mt-3">
              <ScrollArea className="h-72 pr-3">
                <div className="space-y-3">
                  {(sourceLedger?.sources ?? []).length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      No source ledger entries found for this run.
                    </p>
                  ) : (
                    sourceLedger?.sources.map((source) => (
                      <div
                        key={source.source_id}
                        className="rounded-md border p-3 text-sm"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-medium">{source.label}</span>
                          <Badge variant="outline">{source.source_type}</Badge>
                          <Badge variant="neutral">{source.allowed_use}</Badge>
                        </div>
                        {source.clause_number && (
                          <p className="mt-1 text-xs text-muted-foreground">
                            Clause {source.clause_number}
                            {source.clause_title
                              ? `: ${source.clause_title}`
                              : ""}
                          </p>
                        )}
                        {source.snippet && (
                          <p className="mt-2 line-clamp-4 text-xs text-muted-foreground">
                            {source.snippet}
                          </p>
                        )}
                        {source.source_hash && (
                          <p className="mt-2 truncate font-mono text-[11px] text-muted-foreground">
                            {source.source_hash}
                          </p>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </TabsContent>

            <TabsContent value="context" className="mt-3">
              <ScrollArea className="h-72 pr-3">
                <div className="space-y-4 text-sm">
                  <EvidenceList
                    title="Facts"
                    items={contextPack?.facts ?? []}
                    empty="No facts captured."
                  />
                  <EvidenceList
                    title="Required Actions"
                    items={contextPack?.required_actions ?? []}
                    empty="No required actions captured."
                  />
                  <EvidenceList
                    title="Missing Confirmations"
                    items={contextPack?.missing_confirmations ?? []}
                    empty="No missing confirmations."
                  />
                  <div>
                    <p className="font-medium">Contractual Basis</p>
                    {(contextPack?.contractual_basis ?? []).length === 0 ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        No clauses captured.
                      </p>
                    ) : (
                      <div className="mt-2 space-y-2">
                        {contextPack?.contractual_basis?.map((item, index) => (
                          <div
                            key={`${item.source_id ?? index}`}
                            className="rounded-md border p-2 text-xs"
                          >
                            <p className="font-medium">
                              {String(item.clause_number ?? "Clause")}
                              {item.clause_title
                                ? `: ${String(item.clause_title)}`
                                : ""}
                            </p>
                            {item.snippet && (
                              <p className="mt-1 text-muted-foreground">
                                {String(item.snippet)}
                              </p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </ScrollArea>
            </TabsContent>

            <TabsContent value="audit" className="mt-3">
              <ScrollArea className="h-72 pr-3">
                <div className="space-y-3">
                  {(audit?.events ?? []).length === 0 ? (
                    <p className="text-sm text-muted-foreground">
                      No audit events found for this run.
                    </p>
                  ) : (
                    audit?.events.map((event) => (
                      <div
                        key={event.event_id}
                        className="rounded-md border p-3 text-sm"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="font-medium">
                            {event.event_type.replaceAll("_", " ")}
                          </span>
                          {event.status && (
                            <Badge variant="outline">{event.status}</Badge>
                          )}
                        </div>
                        <p className="mt-1 text-xs text-muted-foreground">
                          {event.created_at ? formatDateTime(event.created_at) : ""}
                          {event.actor_user_id ? ` by ${event.actor_user_id}` : ""}
                        </p>
                        {event.detail && (
                          <p className="mt-2 text-xs text-muted-foreground">
                            {event.detail}
                          </p>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </ScrollArea>
            </TabsContent>

            <TabsContent value="governance" className="mt-3">
              <ScrollArea className="h-72 pr-3">
                <div className="space-y-4 text-sm">
                  <div className="rounded-md border p-3">
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <p className="font-medium">Review Assignment</p>
                      {run?.approval_status && (
                        <Badge variant="outline">{run.approval_status}</Badge>
                      )}
                    </div>
                    {run?.assigned_reviewer_id && (
                      <p className="mt-2 text-xs text-muted-foreground">
                        Current reviewer: {run.assigned_reviewer_id}
                      </p>
                    )}
                    <div className="mt-3 grid gap-2">
                      <Input
                        value={reviewerId}
                        onChange={(event) => setReviewerId(event.target.value)}
                        placeholder="Reviewer user ID"
                        disabled={submitting}
                      />
                      <Textarea
                        value={reviewNote}
                        onChange={(event) => setReviewNote(event.target.value)}
                        placeholder="Assignment note"
                        disabled={submitting}
                      />
                      <Button
                        size="sm"
                        className="w-fit gap-2"
                        onClick={() => void handleAssignReviewer()}
                        disabled={submitting || !reviewerId.trim()}
                      >
                        <Users className="h-4 w-4" />
                        Assign
                      </Button>
                    </div>
                    <div className="mt-3 space-y-2">
                      {(governance?.assignments ?? []).map((assignment) => (
                        <div
                          key={assignment.assignment_id}
                          className="rounded-md border p-2 text-xs"
                        >
                          <div className="flex flex-wrap items-center gap-2">
                            <span className="font-medium">
                              {assignment.reviewer_user_id}
                            </span>
                            <Badge variant="outline">{assignment.status}</Badge>
                          </div>
                          {assignment.note && (
                            <p className="mt-1 text-muted-foreground">
                              {assignment.note}
                            </p>
                          )}
                          <p className="mt-1 text-muted-foreground">
                            Assigned {assignment.created_at ? formatDateTime(assignment.created_at) : ""}
                            {assignment.assigned_by ? ` by ${assignment.assigned_by}` : ""}
                          </p>
                        </div>
                      ))}
                    </div>
                  </div>

                  <div className="rounded-md border p-3">
                    <p className="font-medium">Review Comments</p>
                    <Textarea
                      className="mt-3"
                      value={commentBody}
                      onChange={(event) => setCommentBody(event.target.value)}
                      placeholder="Add a review comment"
                      disabled={submitting}
                    />
                    <Button
                      size="sm"
                      variant="outline"
                      className="mt-2 w-fit gap-2"
                      onClick={() => void handleAddComment()}
                      disabled={submitting || !commentBody.trim()}
                    >
                      <MessageSquare className="h-4 w-4" />
                      Comment
                    </Button>
                    <div className="mt-3 space-y-2">
                      {(governance?.comments ?? []).length === 0 ? (
                        <p className="text-xs text-muted-foreground">
                          No review comments recorded.
                        </p>
                      ) : (
                        governance?.comments.map((comment) => (
                          <div
                            key={comment.comment_id}
                            className="rounded-md border p-2 text-xs"
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge variant="neutral">{comment.visibility}</Badge>
                              <span className="text-muted-foreground">
                                {comment.created_at ? formatDateTime(comment.created_at) : ""}
                                {comment.created_by ? ` by ${comment.created_by}` : ""}
                              </span>
                            </div>
                            <p className="mt-2 text-muted-foreground">{comment.body}</p>
                          </div>
                        ))
                      )}
                    </div>
                  </div>

                  <div className="rounded-md border p-3">
                    <p className="font-medium">Return For Correction</p>
                    {run?.returned_reason && (
                      <p className="mt-2 text-xs text-destructive">
                        Last return reason: {run.returned_reason}
                      </p>
                    )}
                    <Textarea
                      className="mt-3"
                      value={returnReason}
                      onChange={(event) => setReturnReason(event.target.value)}
                      placeholder="Correction reason"
                      disabled={submitting}
                    />
                    <Textarea
                      className="mt-2"
                      value={requiredChanges}
                      onChange={(event) => setRequiredChanges(event.target.value)}
                      placeholder="Required changes, one per line"
                      disabled={submitting}
                    />
                    <Button
                      size="sm"
                      variant="destructive"
                      className="mt-2 w-fit gap-2"
                      onClick={() => void handleReturnForCorrection()}
                      disabled={submitting || !returnReason.trim()}
                    >
                      <RotateCcw className="h-4 w-4" />
                      Return
                    </Button>
                  </div>
                </div>
              </ScrollArea>
            </TabsContent>

            <TabsContent value="cyclic" className="mt-3">
              <ScrollArea className="h-72 pr-3">
                <div className="space-y-4 text-sm">
                  {run?.confidence_scores ? (
                    <div className="rounded-md border p-3">
                      <div className="flex flex-wrap items-center justify-between gap-2">
                        <p className="font-medium">Confidence</p>
                        <Badge variant="outline">
                          {Math.round(run.confidence_scores.overall * 100)}% overall
                        </Badge>
                      </div>
                      <div className="mt-2 grid gap-2 text-xs text-muted-foreground">
                        <p>
                          Clause: {Math.round(run.confidence_scores.clause_confidence * 100)}%
                        </p>
                        <p>
                          Factual support: {Math.round(run.confidence_scores.factual_support * 100)}%
                        </p>
                        <p>
                          Tone: {Math.round(run.confidence_scores.tone_suitability * 100)}%
                        </p>
                        <p>Risk: {run.confidence_scores.risk_level}</p>
                      </div>
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">
                      No confidence score is available for this run.
                    </p>
                  )}

                  <div>
                    <p className="font-medium">Iterations</p>
                    {(run?.cyclic_trace ?? []).length === 0 ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        No cyclic trace captured.
                      </p>
                    ) : (
                      <div className="mt-2 space-y-2">
                        {run?.cyclic_trace?.map((item) => (
                          <div
                            key={item.iteration}
                            className="rounded-md border p-2 text-xs"
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span className="font-medium">
                                Iteration {item.iteration}
                              </span>
                              <Badge variant={item.critique_blocking ? "danger" : "outline"}>
                                {item.critique_blocking ? "blocking" : "clear"}
                              </Badge>
                            </div>
                            {item.refinement_queries?.length ? (
                              <p className="mt-1 text-muted-foreground">
                                Refinements: {item.refinement_queries.join(", ")}
                              </p>
                            ) : null}
                            {item.retrieved_source_ids?.length ? (
                              <p className="mt-1 text-muted-foreground">
                                Retrieved: {item.retrieved_source_ids.length} source(s)
                              </p>
                            ) : null}
                            {item.notes && (
                              <p className="mt-1 text-muted-foreground">{item.notes}</p>
                            )}
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  <div>
                    <p className="font-medium">Assertion Support</p>
                    {(run?.assertion_support ?? []).length === 0 ? (
                      <p className="mt-1 text-xs text-muted-foreground">
                        No assertion support records captured.
                      </p>
                    ) : (
                      <div className="mt-2 space-y-2">
                        {run?.assertion_support?.slice(0, 8).map((item) => (
                          <div
                            key={item.assertion_id}
                            className="rounded-md border p-2 text-xs"
                          >
                            <div className="flex flex-wrap items-center gap-2">
                              <Badge variant="outline">{item.support_status}</Badge>
                              <Badge variant="neutral">
                                {item.risk_level ?? "medium"} risk
                              </Badge>
                            </div>
                            <p className="mt-2 text-muted-foreground">{item.text}</p>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              </ScrollArea>
            </TabsContent>
          </Tabs>
        )}
      </CardContent>
    </Card>
  );
}

interface EvidenceListProps {
  title: string;
  items: string[];
  empty: string;
}

function EvidenceList({ title, items, empty }: EvidenceListProps) {
  return (
    <div>
      <p className="font-medium">{title}</p>
      {items.length === 0 ? (
        <p className="mt-1 text-xs text-muted-foreground">{empty}</p>
      ) : (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs text-muted-foreground">
          {items.map((item) => (
            <li key={item}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}
