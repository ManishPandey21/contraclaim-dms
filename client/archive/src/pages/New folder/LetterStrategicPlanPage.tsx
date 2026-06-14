import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Sparkles,
  CheckCircle,
  Edit3,
  RefreshCw,
} from "lucide-react";
import { useLetterWorkflow } from "@/hooks/useLetterWorkflow";
import { useLanggraphDraft } from "@/hooks/useLanggraphDraft";
import { useLetterGraphRuns } from "@/hooks/useLetterGraphRuns";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/hooks/use-toast";
import PlanViewer from "@/components/langgraph/PlanViewer";
import GraphStatusBadge from "@/components/langgraph/GraphStatusBadge";
import BackgroundSummary from "@/components/letter-workflow/BackgroundSummary";
import { LinkedDocumentSelector } from "@/components/letter-workflow/LinkedDocumentSelector";
import type { ContextDocumentSummary } from "@/services/letter-workflow-api";
import type {
  LanggraphBackgroundItem,
  LanggraphContextDocument,
  LanggraphGraphThreadNode,
  LanggraphNodeTrace,
} from "@/types/langgraph";
import { formatDateTime } from "@/utils/dateFormat";
import { mapLetterToUi, UILetter } from "@/utils/letterWorkflowMapping";

type DocumentSummarySource =
  | LanggraphContextDocument
  | ContextDocumentSummary
  | Record<string, unknown>;

const LetterStrategicPlanPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { letters, users, handleLetterUpdate, fetchLetters } =
    useLetterWorkflow();
  const { toast } = useToast();

  const {
    data: existingRun,
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

  const [planDraft, setPlanDraft] = useState("");
  const [summaryPoints, setSummaryPoints] = useState<string[]>([]);
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [selectedDocs, setSelectedDocs] = useState<ContextDocumentSummary[]>(
    []
  );
  const [backgroundItems, setBackgroundItems] = useState<
    LanggraphBackgroundItem[]
  >([]);
  const [runningBackground, setRunningBackground] = useState(false);

  useEffect(() => {
    if (uiLetter) {
      setPlanDraft(uiLetter.strategicPlan ?? "");
      setSummaryPoints(uiLetter.summaryPoints ?? []);
    }
  }, [uiLetter]);

  useEffect(() => {
    if (uiLetter?.contextDocumentIds) {
      setSelectedDocIds(uiLetter.contextDocumentIds);
    }
  }, [uiLetter?.contextDocumentIds]);

  useEffect(() => {
    const latestSummary =
      (existingRun?.background_summary as
        | LanggraphBackgroundItem[]
        | undefined) ??
      (uiLetter?.backgroundSummary as LanggraphBackgroundItem[] | undefined) ??
      [];
    setBackgroundItems(latestSummary);
  }, [
    existingRun?.run_id,
    existingRun?.background_summary,
    uiLetter?.backgroundSummary,
  ]);

  useEffect(() => {
    if (id && uiLetter) {
      fetchRun(id).catch(() => undefined);
    }
  }, [id, uiLetter?.updatedAt, fetchRun]);

  const combinedContextDocuments = useMemo(() => {
    const registry = new Map<string, DocumentSummarySource>();
    for (const doc of selectedDocs) {
      if (!doc?.id) continue;
      registry.set(doc.id, doc);
    }
    const additionalCollections = [
      existingRun?.context_documents,
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
  }, [
    selectedDocs,
    existingRun?.context_documents,
    uiLetter?.contextDocuments,
  ]);

  const runContextIds = useMemo(() => {
    const fromRun = existingRun?.context_document_ids ?? [];
    if (fromRun.length > 0) return fromRun.map(String);
    return (uiLetter?.contextDocumentIds ?? []).map(String);
  }, [existingRun?.context_document_ids, uiLetter?.contextDocumentIds]);

  const backgroundOutdated = useMemo(() => {
    if (!backgroundItems.length) {
      return false;
    }
    const target = [...runContextIds].sort();
    const current = [...selectedDocIds].map(String).sort();
    if (target.length !== current.length) {
      return true;
    }
    return target.some((id, index) => id !== current[index]);
  }, [backgroundItems, runContextIds, selectedDocIds]);

  const backgroundGeneratedLabel = useMemo(() => {
    if (!existingRun?.completed_at) return undefined;
    return `Updated ${formatDateTime(existingRun.completed_at)}`;
  }, [existingRun?.completed_at]);

  const conversationThread = useMemo(
    () =>
      ((existingRun?.graph_thread ??
        uiLetter?.graphThread ??
        []) as LanggraphGraphThreadNode[]) ?? [],
    [existingRun?.graph_thread, uiLetter?.graphThread]
  );

  const handleContextSelection = useCallback(
    (ids: string[], docs: ContextDocumentSummary[]) => {
      setSelectedDocIds(ids);
      setSelectedDocs(docs);
    },
    []
  );

  const handleGenerateBackground = useCallback(async () => {
    if (!id || !uiLetter) return;
    try {
      setRunningBackground(true);
      const response = await runDraft({
        letterId: id,
        subject: uiLetter.subject,
        recipient: uiLetter.recipient,
        context: uiLetter.content,
        points: summaryPoints.length > 0 ? summaryPoints.join("\n") : undefined,
        documentIds: selectedDocIds,
        analysisOnly: true,
      });

      setPlanDraft(response.plan ?? "");
      setSummaryPoints(response.summary_points ?? []);
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
        "Unable to generate background.";
      toast({
        title: "Background generation failed",
        description,
        variant: "destructive",
      });
    } finally {
      setRunningBackground(false);
    }
  }, [
    id,
    runDraft,
    uiLetter?.subject,
    uiLetter?.recipient,
    uiLetter?.content,
    summaryPoints,
    selectedDocIds,
    fetchLetters,
    fetchRun,
    toast,
  ]);

  const handleGeneratePlan = useCallback(async () => {
    if (!id || !uiLetter) return;
    try {
      const response = await runDraft({
        letterId: id,
        subject: uiLetter.subject,
        recipient: uiLetter.recipient,
        context: uiLetter.content,
        points: summaryPoints.length > 0 ? summaryPoints.join("\n") : undefined,
        documentIds: selectedDocIds,
      });

      setPlanDraft(response.plan ?? "");
      setSummaryPoints(response.summary_points ?? []);
      setBackgroundItems(
        (response.background_summary as LanggraphBackgroundItem[]) ?? []
      );

      await fetchLetters();
      await fetchRun(id);

      toast({
        title: "Plan generated",
        description: "LangGraph produced a new strategic plan for this letter.",
      });
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ??
        error?.message ??
        "LangGraph drafting failed.";
      toast({
        title: "Unable to generate plan",
        description,
        variant: "destructive",
      });
    }
  }, [
    id,
    runDraft,
    uiLetter?.subject,
    uiLetter?.recipient,
    uiLetter?.content,
    summaryPoints,
    selectedDocIds,
    fetchLetters,
    fetchRun,
    toast,
  ]);

  const handleSavePlan = useCallback(async () => {
    if (!id) return;
    setSaving(true);
    try {
      await handleLetterUpdate(id, {
        draft_plan: planDraft,
        summary_points: summaryPoints,
      });
      await fetchLetters();
      toast({
        title: "Plan saved",
        description: "Strategic plan updated successfully.",
      });
      setIsEditing(false);
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ??
        error?.message ??
        "Unable to save the plan.";
      toast({
        title: "Save failed",
        description,
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  }, [handleLetterUpdate, fetchLetters, id, planDraft, summaryPoints, toast]);

  const handleProceedToDraft = useCallback(async () => {
    if (!planDraft.trim()) {
      toast({
        title: "Plan required",
        description:
          "Please generate and save a strategic plan before drafting.",
        variant: "destructive",
      });
      return;
    }
    if (isEditing) {
      await handleSavePlan();
    }
    navigate(`/letters/${id}/draft`);
  }, [planDraft, id, handleSavePlan, isEditing, navigate, toast]);

  if (!uiLetter) {
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

  const planHasContent = planDraft.trim().length > 0;
  const trace: LanggraphNodeTrace[] = (existingRun?.trace ??
    uiLetter.draftTrace ??
    []) as LanggraphNodeTrace[];
  const planSummary =
    summaryPoints.length > 0
      ? summaryPoints
      : existingRun?.summary_points ?? [];

  return (
    <div className="container mx-auto p-12 max-w-screen-2xl space-y-6">
      <div>
        <Button variant="ghost" onClick={() => navigate("/letters")}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Letters
        </Button>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-6">
          <div>
            <CardTitle className="text-2xl">{uiLetter.title}</CardTitle>
            <div className="mt-2 space-y-1 text-sm text-muted-foreground">
              <p>
                <strong>To:</strong> {uiLetter.recipient}
              </p>
              <p>
                <strong>Subject:</strong> {uiLetter.subject}
              </p>
            </div>
          </div>
          <div className="flex flex-col items-end gap-2">
            <Badge variant="neutral">Strategic Planning</Badge>
            <GraphStatusBadge
              status={existingRun?.status ?? uiLetter.graphStatus}
            />
          </div>
        </CardHeader>
      </Card>

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
          summaryPoints={planSummary}
          loading={runningBackground}
          highlightOutdated={backgroundOutdated}
          lastGeneratedLabel={backgroundGeneratedLabel}
          actions={
            <Button
              size="sm"
              className="gap-2"
              variant="outline"
              onClick={handleGenerateBackground}
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
          <CardTitle className="flex items-center gap-2 text-xl font-semibold">
            <Sparkles className="h-5 w-5 text-primary" />
            Strategic Plan
          </CardTitle>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              onClick={() => fetchRun(id ?? "")}
              disabled={fetchingRun || !id}
              className="gap-2"
            >
              <RefreshCw
                className={`h-4 w-4 ${fetchingRun ? "animate-spin" : ""}`}
              />
              Refresh Run
            </Button>
            <Button onClick={handleGeneratePlan} disabled={langgraphLoading}>
              {langgraphLoading ? (
                <>
                  <Sparkles className="mr-2 h-4 w-4 animate-spin" />
                  Generating...
                </>
              ) : (
                <>
                  <Sparkles className="mr-2 h-4 w-4" />
                  Generate Strategy Plan
                </>
              )}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {!planHasContent && !langgraphLoading ? (
            <div className="text-center py-12">
              <Sparkles className="h-16 w-16 mx-auto text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">
                No Strategic Plan Yet
              </h3>
              <p className="text-muted-foreground mb-4">
                Generate a LangGraph plan to guide the drafting workflow.
              </p>
            </div>
          ) : (
            <div className="space-y-4">
              {isEditing ? (
                <>
                  <Textarea
                    value={planDraft}
                    onChange={(event) => setPlanDraft(event.target.value)}
                    className="min-h-[400px] font-mono text-sm"
                  />
                  <div className="flex gap-2">
                    <Button onClick={handleSavePlan} disabled={saving}>
                      <CheckCircle className="mr-2 h-4 w-4" />
                      {saving ? "Saving..." : "Save Changes"}
                    </Button>
                    <Button
                      variant="outline"
                      onClick={() => {
                        setPlanDraft(uiLetter.strategicPlan ?? "");
                        setIsEditing(false);
                      }}
                    >
                      Cancel
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  <PlanViewer
                    plan={planDraft}
                    summaryPoints={planSummary}
                    documents={combinedContextDocuments}
                    trace={trace}
                  />
                  <div className="flex flex-wrap items-center gap-2 justify-between">
                    <Button
                      variant="outline"
                      onClick={() => setIsEditing(true)}
                    >
                      <Edit3 className="mr-2 h-4 w-4" />
                      Edit Plan
                    </Button>
                    <div className="flex gap-2">
                      <Button
                        variant="outline"
                        onClick={handleGeneratePlan}
                        disabled={langgraphLoading}
                      >
                        <Sparkles className="mr-2 h-4 w-4" />
                        Regenerate Plan
                      </Button>
                      <Button onClick={handleProceedToDraft} className="gap-2">
                        <CheckCircle className="h-4 w-4" />
                        Approve &amp; Proceed
                      </Button>
                    </div>
                  </div>
                </>
              )}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default LetterStrategicPlanPage;
