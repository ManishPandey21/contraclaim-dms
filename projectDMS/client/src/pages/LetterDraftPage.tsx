import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, RefreshCw, Sparkles } from "lucide-react";
import { useLetterWorkflow } from "@/hooks/useLetterWorkflow";
import { useLetterGraphRuns } from "@/hooks/useLetterGraphRuns";
import { useLanggraphDraft } from "@/hooks/useLanggraphDraft";
import LetterDraftEditor from "@/components/letter-workflow/LetterDraftEditor";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/hooks/use-toast";
import PlanViewer from "@/components/langgraph/PlanViewer";
import GraphStatusBadge from "@/components/langgraph/GraphStatusBadge";
import { LinkedDocumentSelector } from "@/components/letter-workflow/LinkedDocumentSelector";
import BackgroundSummary from "@/components/letter-workflow/BackgroundSummary";
import DraftSourcesPanel from "@/components/letter-workflow/DraftSourcesPanel";
import type { ContextDocumentSummary } from "@/services/letter-workflow-api";
import type {
  LanggraphBackgroundItem,
  LanggraphContextDocument,
  LanggraphGraphThreadNode,
  LanggraphDraftReviewFinding,
  LanggraphDraftSource,
} from "@/types/langgraph";
import { formatDateTime } from "@/utils/dateFormat";
import { mapLetterToUi, UILetter } from "@/utils/letterWorkflowMapping";
import { Textarea } from "@/components/ui/textarea";
import { AlertTriangle } from "lucide-react";

type DocumentSummarySource =
  | LanggraphContextDocument
  | ContextDocumentSummary
  | Record<string, unknown>;

const LetterDraftPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { letters, users, handleLetterUpdate, submitForReview, fetchLetters } =
    useLetterWorkflow();
  const { toast } = useToast();

  const {
    data: graphRun,
    fetchRun,
    loading: fetchingRun,
  } = useLetterGraphRuns();

  const { runDraft, loading: langgraphLoading } = useLanggraphDraft();

  const letter = useMemo(
    () => letters.find((entry) => entry.id === id),
    [letters, id]
  );

  const uiLetter: UILetter | null = useMemo(
    () => (letter ? mapLetterToUi(letter, users) : null),
    [letter, users]
  );

  const editorLetter = useMemo(() => {
    if (!uiLetter) return null;
    const content =
      graphRun?.draft?.body ?? uiLetter.draftBody ?? uiLetter.content;
    return {
      ...uiLetter,
      content,
    };
  }, [uiLetter, graphRun]);

  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [selectedDocs, setSelectedDocs] = useState<ContextDocumentSummary[]>(
    []
  );
  const [backgroundItems, setBackgroundItems] = useState<
    LanggraphBackgroundItem[]
  >([]);
  const [runningBackground, setRunningBackground] = useState(false);
  const [processingSubmission, setProcessingSubmission] = useState(false);
  const [planOverride, setPlanOverride] = useState<string>("");
  const [linkedLetterCodes, setLinkedLetterCodes] = useState<string[]>([]);
  const [manualLinkedCode, setManualLinkedCode] = useState("");

  useEffect(() => {
    if (id && uiLetter) {
      fetchRun(id).catch(() => undefined);
    }
  }, [id, fetchRun, uiLetter]);

  useEffect(() => {
    const planText = graphRun?.plan ?? uiLetter?.strategicPlan ?? "";
    if (planText) {
      setPlanOverride(planText);
    }
    const codes =
      (graphRun?.graph_thread ?? uiLetter?.graphThread ?? [])
        .map((node: LanggraphGraphThreadNode) => String(node.normCode || node.code || ""))
        .filter(Boolean);
    if (codes.length) {
      setLinkedLetterCodes(Array.from(new Set(codes)));
    }
  }, [graphRun?.plan, graphRun?.graph_thread, uiLetter?.strategicPlan, uiLetter?.graphThread]);

  useEffect(() => {
    if (!uiLetter) return;
    if (uiLetter.status === "Strategy" || !uiLetter.strategicPlan) {
      navigate(`/letters/${id}/strategy`, { replace: true });
    }
  }, [uiLetter, id, navigate]);

  useEffect(() => {
    if (uiLetter?.contextDocumentIds) {
      setSelectedDocIds(uiLetter.contextDocumentIds);
    }
  }, [uiLetter]);

  useEffect(() => {
    const latestSummary =
      (graphRun?.background_summary as LanggraphBackgroundItem[] | undefined) ??
      (uiLetter?.backgroundSummary as LanggraphBackgroundItem[] | undefined) ??
      [];
    setBackgroundItems(latestSummary);
  }, [graphRun, uiLetter]);

  const conversationThread = useMemo(
    () =>
      ((graphRun?.graph_thread ??
        uiLetter?.graphThread ??
        []) as LanggraphGraphThreadNode[]) ?? [],
    [graphRun?.graph_thread, uiLetter?.graphThread]
  );

  const graphThreadCodes = useMemo(() => {
    return conversationThread
      .map((node) => String(node.normCode || node.code || ""))
      .filter(Boolean);
  }, [conversationThread]);

  const combinedContextDocuments = useMemo(() => {
    const registry = new Map<string, DocumentSummarySource>();
    for (const doc of selectedDocs) {
      if (!doc?.id) continue;
      registry.set(doc.id, doc);
    }
    const additionalCollections = [
      graphRun?.context_documents,
      uiLetter?.contextDocuments,
    ] as (LanggraphContextDocument[] | undefined)[];
    for (const collection of additionalCollections) {
      if (!collection) continue;
      for (const item of collection) {
        const payload = item as Record<string, unknown>;
        const rawId =
          payload.id ??
          payload.documentId ??
          payload.document_id ??
          payload._id;
        if (!rawId) continue;
        const key = String(rawId);
        if (!registry.has(key)) {
          registry.set(key, item as DocumentSummarySource);
        }
      }
    }
    return Array.from(registry.values());
  }, [selectedDocs, graphRun?.context_documents, uiLetter?.contextDocuments]);

  const runContextIds = useMemo(() => {
    const fromRun = graphRun?.context_document_ids ?? [];
    if (fromRun.length > 0) return fromRun.map(String);
    return (uiLetter?.contextDocumentIds ?? []).map(String);
  }, [graphRun?.context_document_ids, uiLetter?.contextDocumentIds]);

  const backgroundOutdated = useMemo(() => {
    if (!backgroundItems.length) {
      return false;
    }
    const expected = [...runContextIds].sort();
    const current = [...selectedDocIds].map(String).sort();
    if (expected.length !== current.length) {
      return true;
    }
    return expected.some((id, index) => id !== current[index]);
  }, [backgroundItems, runContextIds, selectedDocIds]);

  const backgroundGeneratedLabel = useMemo(() => {
    if (!graphRun?.completed_at) return undefined;
    return `Updated ${formatDateTime(graphRun.completed_at)}`;
  }, [graphRun?.completed_at]);

  const summaryPoints = useMemo(
    () => graphRun?.summary_points ?? uiLetter?.summaryPoints ?? [],
    [graphRun?.summary_points, uiLetter]
  );

  const draftSources = useMemo(
    () =>
      (graphRun?.sources as LanggraphDraftSource[] | undefined) ??
      (uiLetter?.draftSources as LanggraphDraftSource[] | undefined) ??
      [],
    [graphRun?.sources, uiLetter?.draftSources]
  );

  const reviewerFindings = useMemo(
    () =>
      (graphRun?.reviewer_findings as LanggraphDraftReviewFinding[] | undefined) ??
      (uiLetter?.reviewerFindings as LanggraphDraftReviewFinding[] | undefined) ??
      [],
    [graphRun?.reviewer_findings, uiLetter?.reviewerFindings]
  );

  const reviewerBlocking =
    graphRun?.reviewer_blocking ??
    (uiLetter as any)?.reviewerBlocking ??
    reviewerFindings.some((f) => f.level === "error");

  const draftVersions = useMemo(() => {
    const versions =
      ((uiLetter as any)?.draftVersions as Record<string, any>[] | undefined) ??
      [];
    return versions
      .map((v) => ({
        version: v.version,
        status: v.status,
        body: v.body ?? "",
        plan: v.plan ?? "",
        created_at: v.created_at ?? v.createdAt,
        created_by: v.created_by ?? v.createdBy,
        reviewer_findings: v.reviewer_findings ?? [],
      }))
      .filter((v) => typeof v.version === "number")
      .sort((a, b) => b.version - a.version);
  }, [uiLetter]);

  const referenceLetters = useMemo(() => {
    return letters
      .filter((entry) => entry.id !== id)
      .map((entry) => ({
        id: entry.id,
        title: entry.title ?? "",
        subject: entry.subject ?? "",
        referenceNumber:
          entry.letter_no ?? (entry as any).letterNo ?? entry.id,
        recipient: entry.recipient ?? "",
        createdAt: entry.created_at ?? entry.updated_at ?? undefined,
        status: entry.status,
      }))
      .sort((a, b) => {
        const aTime = a.createdAt ? new Date(a.createdAt).getTime() : 0;
        const bTime = b.createdAt ? new Date(b.createdAt).getTime() : 0;
        return bTime - aTime;
      });
  }, [letters, id]);

  // All useCallback hooks must be defined before any conditional returns
  const handleContextSelection = useCallback(
    (ids: string[], docs: ContextDocumentSummary[]) => {
      setSelectedDocIds(ids);
      setSelectedDocs(docs);
    },
    []
  );

  const handleToggleLinkedCode = useCallback(
    (code: string) => {
      setLinkedLetterCodes((prev) => {
        const exists = prev.includes(code);
        if (exists) return prev.filter((item) => item !== code);
        return [...prev, code];
      });
    },
    []
  );

  const handleAddManualLinkedCode = useCallback(() => {
    const code = manualLinkedCode.trim();
    if (!code) return;
    setLinkedLetterCodes((prev) =>
      prev.includes(code) ? prev : [...prev, code]
    );
    setManualLinkedCode("");
  }, [manualLinkedCode]);

  const handleRunBackground = useCallback(async () => {
    if (!id || !uiLetter || !editorLetter) return;
    try {
      setRunningBackground(true);
      const summaryLines =
        graphRun?.summary_points ?? uiLetter.summaryPoints ?? [];
      const response = await runDraft({
        letterId: id,
        subject: uiLetter.subject,
        recipient: uiLetter.recipient,
        context: editorLetter.content,
        points: summaryLines.length > 0 ? summaryLines.join("\n") : undefined,
        documentIds: selectedDocIds,
        analysisOnly: true,
      });
      setBackgroundItems(
        (response.background_summary as LanggraphBackgroundItem[]) ?? []
      );
      await fetchLetters();
      await fetchRun(id);
      toast({
        title: "Background generated",
        description: "AI background summary refreshed successfully.",
      });
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ??
        error?.message ??
        "LangGraph background generation failed.";
      toast({
        title: "Unable to generate background",
        description,
        variant: "destructive",
      });
    } finally {
      setRunningBackground(false);
    }
  }, [
    id,
    runDraft,
    graphRun,
    uiLetter,
    editorLetter,
    selectedDocIds,
    fetchLetters,
    fetchRun,
    toast,
  ]);

  const handleRunDraft = useCallback(async () => {
    if (!id || !uiLetter || !editorLetter) return;
    try {
      const summaryLines =
        graphRun?.summary_points ?? uiLetter.summaryPoints ?? [];
      const response = await runDraft({
        letterId: id,
        subject: uiLetter.subject,
        recipient: uiLetter.recipient,
        context: editorLetter.content,
        points: summaryLines.length > 0 ? summaryLines.join("\n") : undefined,
        documentIds: selectedDocIds,
        planOverride: planOverride || undefined,
        includeLetterCodes: linkedLetterCodes,
        excludeLetterCodes: graphThreadCodes.filter(
          (code) => code && !linkedLetterCodes.includes(code)
        ),
      });
      setBackgroundItems(
        (response.background_summary as LanggraphBackgroundItem[]) ?? []
      );
      await fetchLetters();
      await fetchRun(id);
      toast({
        title: "Draft updated",
        description: "LangGraph generated a new draft version.",
      });
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ??
        error?.message ??
        "LangGraph drafting failed.";
      toast({
        title: "Unable to generate draft",
        description,
        variant: "destructive",
      });
    }
  }, [
    id,
    uiLetter,
    editorLetter,
    runDraft,
    graphRun,
    selectedDocIds,
    planOverride,
    linkedLetterCodes,
    graphThreadCodes,
    fetchLetters,
    fetchRun,
    toast,
  ]);

  const handleEditorUpdate = useCallback(
    async (updatedLetter: any) => {
      if (!id || !uiLetter) return;

      if (
        updatedLetter.status === "Review" &&
        (reviewerBlocking ||
          reviewerFindings.some((finding) => finding.level === "error"))
      ) {
        toast({
          title: "Blocked by reviewer",
          description:
            "AI reviewer flagged blocking issues. Resolve findings before submitting for review.",
          variant: "destructive",
        });
        return;
      }

      const patch: Record<string, unknown> = {
        content: updatedLetter.content,
      };
      if (updatedLetter.reference) {
        patch.reference = {
          id: updatedLetter.reference.id,
          title: updatedLetter.reference.title,
          subject: updatedLetter.reference.subject,
          date: updatedLetter.reference.date,
          reference_number:
            updatedLetter.reference.referenceNumber ??
            updatedLetter.reference.reference_number,
        };
      } else {
        patch.reference = null;
      }

      await handleLetterUpdate(id, patch);
      await fetchLetters();

      if (updatedLetter.status === "Review" && uiLetter.status !== "Review") {
        setProcessingSubmission(true);
        try {
          const reviewerSummary =
            reviewerFindings.length > 0
              ? `Reviewer findings:\n${reviewerFindings
                  .map((finding) => {
                    const level = finding.level.toUpperCase();
                    const evidence = finding.evidence
                      ? ` (${finding.evidence})`
                      : "";
                    return `${level}: ${finding.message}${evidence}`;
                  })
                  .join("\n")}`
              : undefined;
          await submitForReview(id, {
            reviewer_summary: reviewerSummary,
            reviewer_findings: reviewerFindings,
          });
          await fetchLetters();
          toast({
            title: "Submitted for review",
            description: "Draft sent to reviewers successfully.",
          });
          navigate(`/letters/${id}/review`);
        } finally {
          setProcessingSubmission(false);
        }
      } else {
        toast({
          title: "Draft saved",
          description: "Changes stored successfully.",
        });
      }
    },
    [
      id,
      handleLetterUpdate,
      fetchLetters,
      submitForReview,
      uiLetter,
      navigate,
      toast,
      reviewerFindings,
      reviewerBlocking,
    ]
  );

  const handleCancel = useCallback(() => {
    navigate("/letters");
  }, [navigate]);

  // Early return AFTER all hooks
  if (!uiLetter || !editorLetter) {
    return (
      <div className="container mx-auto p-6">
        <Card>
          <CardHeader>
            <CardTitle>Letter Not Found</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="text-muted-foreground mb-4">
              The requested letter could not be found.
            </p>
            <Button onClick={() => navigate("/letters")}>
              <ArrowLeft className="mr-2 h-4 w-4" />
              Back to Letters
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6 space-y-6">
      <div>
        <Button variant="ghost" onClick={() => navigate("/letters")}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Letters
        </Button>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between">
          <div>
            <CardTitle className="text-2xl">{uiLetter.title}</CardTitle>
            <div className="mt-2 text-sm text-muted-foreground space-y-1">
              <p>
                <strong>Recipient:</strong> {uiLetter.recipient}
              </p>
              <p>
                <strong>Subject:</strong> {uiLetter.subject}
              </p>
            </div>
          </div>
          <GraphStatusBadge status={graphRun?.status ?? uiLetter.graphStatus} />
        </CardHeader>
      </Card>

      {reviewerBlocking && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm flex items-start gap-2">
          <AlertTriangle className="h-4 w-4 text-destructive mt-0.5" />
          <div>
            <p className="font-semibold text-destructive">
              Submission blocked by reviewer findings
            </p>
            <p className="text-muted-foreground">
              Fix the issues flagged by the AI reviewer before moving this draft
              to Review.
            </p>
          </div>
        </div>
      )}

      <div className="grid gap-6 lg:grid-cols-2">
        <LinkedDocumentSelector
          letterId={id ?? ""}
          organizationId={uiLetter.organizationId}
          projectId={uiLetter.projectId}
          activeLetterCode={uiLetter.letterNo}
          conversationThread={conversationThread}
          onSelectionChange={handleContextSelection}
        />
        <BackgroundSummary
          entries={backgroundItems}
          documents={combinedContextDocuments}
          summaryPoints={summaryPoints}
          loading={runningBackground}
          highlightOutdated={backgroundOutdated}
          lastGeneratedLabel={backgroundGeneratedLabel}
          actions={
            <Button
              size="sm"
              variant="outline"
              className="gap-2"
              onClick={handleRunBackground}
              disabled={runningBackground || langgraphLoading}
            >
              {runningBackground ? (
                <>
                  <Sparkles className="h-4 w-4 animate-spin" />
                  Generating...
                </>
              ) : (
                <>
                  <Sparkles className="h-4 w-4" />
                  Generate Background
                </>
              )}
            </Button>
          }
        />
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <CardTitle className="text-lg font-semibold">
            LangGraph Strategic Plan
          </CardTitle>
          <div className="flex gap-2">
            <Button
              variant="outline"
              onClick={() => fetchRun(id ?? "")}
              disabled={fetchingRun}
              className="gap-2"
            >
              <RefreshCw
                className={`h-4 w-4 ${fetchingRun ? "animate-spin" : ""}`}
              />
              Refresh
            </Button>
            <Button
              variant="outline"
              onClick={handleRunDraft}
              disabled={langgraphLoading}
              className="gap-2"
            >
              <Sparkles
                className={`h-4 w-4 ${langgraphLoading ? "animate-spin" : ""}`}
              />
              {langgraphLoading ? "Generating..." : "Regenerate Draft"}
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <PlanViewer
            plan={uiLetter.strategicPlan}
            summaryPoints={summaryPoints}
            documents={combinedContextDocuments}
            trace={graphRun?.trace ?? uiLetter.draftTrace}
          />
          <div className="mt-4 space-y-2">
            <p className="text-sm font-medium">Plan override (editable)</p>
            <p className="text-xs text-muted-foreground">
              Update the plan before drafting. This will be sent to the model (plan uses Grok by default).
            </p>
            <Textarea
              value={planOverride}
              onChange={(e) => setPlanOverride(e.target.value)}
              placeholder="Edit or paste a plan for this draft run..."
              className="min-h-[140px]"
            />
          </div>
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-[2fr_1fr]">
        <Card>
          <CardHeader>
            <CardTitle>Draft Workspace</CardTitle>
          </CardHeader>
          <CardContent>
            <LetterDraftEditor
              letter={editorLetter}
              referenceLetters={referenceLetters}
              onSave={handleEditorUpdate}
              onCancel={handleCancel}
            />
            {processingSubmission && (
              <p className="mt-4 text-sm text-muted-foreground">
                Submitting draft for review...
              </p>
            )}
          </CardContent>
        </Card>
        <div className="space-y-4">
          <DraftSourcesPanel
            sources={draftSources}
            reviewerFindings={reviewerFindings}
          />

          <Card>
            <CardHeader>
              <CardTitle className="text-sm font-semibold">
                Draft Versions
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {draftVersions.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                  No previous versions yet. Regenerate to create snapshots.
                </p>
              ) : (
                draftVersions.map((version) => (
                  <div
                    key={version.version}
                    className="rounded-md border p-3 space-y-1"
                  >
                    <div className="flex items-center justify-between text-sm">
                      <span className="font-semibold">
                        v{version.version} • {version.status}
                      </span>
                      <span className="text-muted-foreground">
                        {version.created_at
                          ? formatDateTime(version.created_at)
                          : ""}
                      </span>
                    </div>
                    <p className="text-xs text-muted-foreground line-clamp-3">
                      {version.body}
                    </p>
                    {Array.isArray(version.reviewer_findings) &&
                      version.reviewer_findings.length > 0 && (
                        <p className="text-xs text-amber-600">
                          Reviewer notes: {version.reviewer_findings.length}
                        </p>
                      )}
                  </div>
                ))
              )}
            </CardContent>
          </Card>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg font-semibold">
            Linked Letters (Falkor Graph)
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <p className="text-sm text-muted-foreground">
            Select which linked letters from Falkor to include as context. Uncheck to exclude. Add extra codes if needed.
          </p>
          <div className="space-y-2">
            {graphThreadCodes.length === 0 ? (
              <p className="text-sm text-muted-foreground">No linked letters found.</p>
            ) : (
              graphThreadCodes.map((code) => (
                <label key={code} className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={linkedLetterCodes.includes(code)}
                    onChange={() => handleToggleLinkedCode(code)}
                  />
                  <span>{code}</span>
                </label>
              ))
            )}
          </div>
          <div className="flex items-center gap-2 pt-2">
            <input
              type="text"
              value={manualLinkedCode}
              onChange={(e) => setManualLinkedCode(e.target.value)}
              placeholder="Add letter code"
              className="flex-1 border rounded-md px-3 py-2 text-sm"
            />
            <Button variant="outline" size="sm" onClick={handleAddManualLinkedCode}>
              Add
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default LetterDraftPage;
