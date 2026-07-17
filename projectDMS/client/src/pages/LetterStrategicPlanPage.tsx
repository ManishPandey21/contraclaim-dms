import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, Sparkles, CheckCircle, Edit3, Loader2 } from "lucide-react";
import { useLetterWorkflow } from "@/hooks/useLetterWorkflow";
import { useLanggraphDraft } from "@/hooks/useLanggraphDraft";
import { useLetterDrafting } from "@/hooks/useLetterDrafting";
import { LANGGRAPH_ENABLED } from "@/config/features";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/hooks/use-toast";
import PlanViewer from "@/components/langgraph/PlanViewer";
import GraphStatusBadge from "@/components/langgraph/GraphStatusBadge";
import StrategyPlanDisplay from "@/components/langgraph/StrategyPlanDisplay";
import RoleSelector from "@/components/letter-workflow/RoleSelector";
import ContextPreviewTabs from "@/components/letter-workflow/ContextPreviewTabs";
import BackgroundSummary from "@/components/letter-workflow/BackgroundSummary";
import { LinkedDocumentSelector } from "@/components/letter-workflow/LinkedDocumentSelector";
import { generateStrategyContexts, saveStrategyRole } from "@/services/strategy";
import type { ContextDocumentSummary } from "@/services/letter-workflow-api";
import type {
  LanggraphBackgroundItem,
  LanggraphContextDocument,
  LanggraphGraphThreadNode,
  LanggraphNodeTrace,
} from "@/types/langgraph";
import { formatDateTime } from "@/utils/dateFormat";
import { mapLetterToUi, UILetter } from "@/utils/letterWorkflowMapping";
import type {
  StrategyContextResponse,
  StrategyPlanResponse,
  StrategyRole,
} from "@/types/strategyPlan";
import type { DraftRunResponse, SourceEvidence } from "@/types/letterDrafting";

type DocumentSummarySource =
  | LanggraphContextDocument
  | ContextDocumentSummary
  | Record<string, unknown>;

const backgroundItemsFromDraftRun = (
  run: DraftRunResponse
): LanggraphBackgroundItem[] => {
  const entries: LanggraphBackgroundItem[] = [];
  const currentMaterials = Array.isArray(run.context_bundle?.current_materials)
    ? run.context_bundle.current_materials
    : [];
  currentMaterials.slice(0, 4).forEach((text, index) => {
    if (typeof text !== "string" || !text.trim()) return;
    entries.push({
      id: `current-${index}`,
      type: "summary",
      text,
      generated_at: run.completed_at,
    });
  });
  (run.sources ?? []).slice(0, 12).forEach((source: SourceEvidence, index) => {
    entries.push({
      id: source.source_id || `source-${index}`,
      type:
        source.source_type === "contract_clause"
          ? "clause"
          : source.source_type === "prior_correspondence" ||
              source.source_type === "graph_thread"
            ? "letter"
            : source.source_type === "comment"
              ? "comment"
              : "document",
      text: source.snippet || source.text || source.label,
      documents: source.document_id ? [source.document_id] : undefined,
      generated_at: run.completed_at,
    });
  });
  return entries;
};

const strategyPlanFromDraftRun = (
  run: DraftRunResponse,
  fallback: UILetter,
  background: LanggraphBackgroundItem[]
): StrategyPlanResponse => ({
  letter_id: run.letter_id,
  run_id: run.run_id,
  status: run.status,
  generated_at: run.completed_at ?? run.started_at ?? new Date().toISOString(),
  plan: run.plan ?? "",
  tone_approach: {},
  content_structure: (run.planning_sheet ?? {}) as Record<string, any>,
  specific_responses: Array.isArray(run.reply_matrix)
    ? (run.reply_matrix as Record<string, any>[])
    : [],
  risk_mitigation: {},
  desired_outcome: {},
  summary_points:
    ((run.source_integrity_summary?.key_points as string[] | undefined) ??
      fallback.summaryPoints ??
      []),
  background_summary: background,
  context_document_ids:
    run.context_bundle?.selected_document_ids ?? fallback.contextDocumentIds ?? [],
  context_documents: fallback.contextDocuments ?? [],
  trace: (run.trace ?? []).map((entry, index) => ({
    name: String(entry.stage ?? `stage-${index + 1}`),
    status: String(entry.status ?? "success"),
    started_at: run.started_at ?? new Date().toISOString(),
    completed_at: run.completed_at ?? run.started_at ?? new Date().toISOString(),
    data: entry,
  })),
  warnings: run.warnings ?? [],
});

const isLanggraphNodeTrace = (value: unknown): value is LanggraphNodeTrace => {
  if (!value || typeof value !== "object") {
    return false;
  }
  const node = value as Record<string, unknown>;
  return (
    typeof node.name === "string" &&
    typeof node.status === "string" &&
    typeof node.started_at === "string" &&
    typeof node.completed_at === "string" &&
    typeof node.data === "object" &&
    node.data !== null
  );
};

const normalizeTraceSource = (source: unknown): LanggraphNodeTrace[] => {
  if (!Array.isArray(source)) {
    return [];
  }
  return source.filter(isLanggraphNodeTrace);
};

const deriveTraceFromSources = (
  ...sources: unknown[]
): LanggraphNodeTrace[] => {
  for (const source of sources) {
    const trace = normalizeTraceSource(source);
    if (trace.length) {
      return trace;
    }
  }
  return [];
};

const LetterStrategicPlanPage = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { letters, users, currentUser, handleLetterUpdate, fetchLetters } =
    useLetterWorkflow();
  const { toast } = useToast();

  const { loading: langgraphLoading } = useLanggraphDraft();
  const {
    run: runDraftingWorkflow,
    preparePlan,
    acceptPlan,
    loading: draftingLoading,
  } = useLetterDrafting();
  const strategyGenerating = draftingLoading || (LANGGRAPH_ENABLED && langgraphLoading);

  const rawLetter = useMemo(
    () => letters.find((entry) => entry.id === id),
    [letters, id]
  );
  const uiLetter: UILetter | null = useMemo(
    () => (rawLetter ? mapLetterToUi(rawLetter, users) : null),
    [rawLetter, users]
  );

  const [cachedLetter, setCachedLetter] = useState<UILetter | null>(null);

  useEffect(() => {
    if (uiLetter) {
      setCachedLetter(uiLetter);
    }
  }, [uiLetter]);

  const viewLetter = uiLetter ?? cachedLetter;

  const letterPlan = useMemo<StrategyPlanResponse | null>(() => {
    if (!viewLetter?.strategicPlan) {
      return null;
    }
    const outline = (viewLetter.strategicOutline ?? {}) as Record<string, any>;
    const tracePayload = deriveTraceFromSources(
      viewLetter.strategyGraphTrace,
      viewLetter.draftTrace
    );
    return {
      letter_id: viewLetter.id,
      run_id: viewLetter.strategyRunId ?? viewLetter.graphRunId ?? "",
      status: viewLetter.strategyGraphStatus ?? "analysis_only",
      generated_at:
        viewLetter.strategyGraphCompletedAt ??
        viewLetter.strategyGraphStartedAt ??
        viewLetter.updatedAt,
      plan: viewLetter.strategicPlan ?? "",
      tone_approach: (outline.tone_approach as Record<string, any>) ?? {},
      content_structure:
        (outline.content_structure as Record<string, any>) ?? {},
      specific_responses: Array.isArray(outline.specific_responses)
        ? (outline.specific_responses as Record<string, any>[])
        : [],
      risk_mitigation: (outline.risk_mitigation as Record<string, any>) ?? {},
      desired_outcome: (outline.desired_outcome as Record<string, any>) ?? {},
      summary_points: viewLetter.summaryPoints ?? [],
      background_summary: (viewLetter.backgroundSummary ??
        []) as LanggraphBackgroundItem[],
      context_document_ids: viewLetter.contextDocumentIds ?? [],
      context_documents: (viewLetter.contextDocuments ??
        []) as LanggraphContextDocument[],
      trace: tracePayload,
      warnings: [],
    };
  }, [viewLetter]);

  const [strategyPlanData, setStrategyPlanData] = useState<StrategyPlanResponse | null>(null);
  const [planDraft, setPlanDraft] = useState("");
  const [summaryPoints, setSummaryPoints] = useState<string[]>([]);
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [selectedDocIds, setSelectedDocIds] = useState<string[]>([]);
  const [selectedDocs, setSelectedDocs] = useState<ContextDocumentSummary[]>(
    []
  );
  const [selectedRole, setSelectedRole] = useState<StrategyRole>("contractor");
  const [engineerRecipient, setEngineerRecipient] = useState("Contractor");
  const [contextBundle, setContextBundle] =
    useState<StrategyContextResponse | null>(null);
  const [contextLoading, setContextLoading] = useState(false);
  const [backgroundItems, setBackgroundItems] = useState<
    LanggraphBackgroundItem[]
  >([]);
  const [runningBackground, setRunningBackground] = useState(false);

  const aggregatedContexts = {
    contractor:
      contextBundle?.contractor_context ?? viewLetter?.contractorContext ?? "",
    engineer:
      contextBundle?.engineer_context ?? viewLetter?.engineerContext ?? "",
    employer:
      contextBundle?.employer_context ?? viewLetter?.employerContext ?? "",
  };

  const hasContexts = Object.values(aggregatedContexts).some(
    (value) => value && value.trim().length > 0
  );

  const normalizeRole = useCallback((role?: string | null): StrategyRole => {
    if (!role) return "contractor";
    const value = role.toLowerCase();
    if (value.startsWith("engineer")) return "engineer";
    if (value.startsWith("employer")) return "employer";
    return "contractor";
  }, []);

  const normalizeRecipient = useCallback((value?: string | null): string => {
    if (!value) return "Contractor";
    return value.toLowerCase().startsWith("employer") ? "Employer" : "Contractor";
  }, []);

  useEffect(() => {
    if (!uiLetter) return;
    setSelectedRole(normalizeRole(uiLetter.strategyRole));
    setEngineerRecipient(normalizeRecipient(uiLetter.strategyRecipient));
    if (
      uiLetter.contractorContext ||
      uiLetter.engineerContext ||
      uiLetter.employerContext
    ) {
      setContextBundle((prev) => {
        if (prev && prev.letter_id === uiLetter.id) {
          return prev;
        }
        return {
          letter_id: uiLetter.id,
          contractor_context: uiLetter.contractorContext,
          engineer_context: uiLetter.engineerContext,
          employer_context: uiLetter.employerContext,
          thread_letters: uiLetter.threadLetters ?? [],
          timeline: [],
        };
      });
    }
  }, [
    normalizeRecipient,
    normalizeRole,
    uiLetter,
    uiLetter?.id,
    uiLetter?.strategyRole,
    uiLetter?.strategyRecipient,
    uiLetter?.contractorContext,
    uiLetter?.engineerContext,
    uiLetter?.employerContext,
    uiLetter?.threadLetters,
  ]);

  useEffect(() => {
    if (letterPlan) {
      setStrategyPlanData(letterPlan);
    } else {
      setStrategyPlanData(null);
    }
  }, [letterPlan]);

  useEffect(() => {
    if (strategyPlanData) {
      setPlanDraft(strategyPlanData.plan ?? "");
      setSummaryPoints(strategyPlanData.summary_points ?? []);
    }
  }, [strategyPlanData]);

  useEffect(() => {
    if (uiLetter?.contextDocumentIds) {
      setSelectedDocIds(uiLetter.contextDocumentIds);
    }
  }, [uiLetter?.contextDocumentIds]);

  useEffect(() => {
    const latestSummary =
      (strategyPlanData?.background_summary as
        | LanggraphBackgroundItem[]
        | undefined) ??
      (uiLetter?.backgroundSummary as LanggraphBackgroundItem[] | undefined) ??
      [];
    setBackgroundItems(latestSummary);
  }, [strategyPlanData?.background_summary, uiLetter?.backgroundSummary]);

  const persistStrategyRole = useCallback(
    async (role: StrategyRole, recipient?: string) => {
      if (!id) return;
      try {
        await saveStrategyRole(id, role, recipient);
        await fetchLetters();
      } catch (error: any) {
        const description =
          error?.response?.data?.detail ??
          error?.message ??
          "Unable to save role preference.";
        toast({
          title: "Role update failed",
          description,
          variant: "destructive",
        });
      }
    },
    [id, fetchLetters, toast]
  );

  const handleRoleChange = useCallback(
    async (role: StrategyRole) => {
      setSelectedRole(role);
      const nextRecipient = role === "engineer" ? engineerRecipient : undefined;
      if (role !== "engineer") {
        setEngineerRecipient("Contractor");
      }
      await persistStrategyRole(role, nextRecipient);
    },
    [engineerRecipient, persistStrategyRole]
  );

  const handleEngineerRecipientChange = useCallback(
    async (value: string) => {
      setEngineerRecipient(value);
      if (selectedRole === "engineer") {
        await persistStrategyRole(selectedRole, value);
      }
    },
    [persistStrategyRole, selectedRole]
  );

  const handleGenerateContexts = useCallback(async () => {
    if (!id) return;
    setContextLoading(true);
    try {
      const response = await generateStrategyContexts(id, { refresh: true });
      setContextBundle(response);
      await fetchLetters();
      toast({
        title: "Contexts generated",
        description: "Contractor, Engineer, and Employer perspectives updated.",
      });
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ??
        error?.message ??
        "Unable to generate contexts.";
      toast({
        title: "Context generation failed",
        description,
        variant: "destructive",
      });
    } finally {
      setContextLoading(false);
    }
  }, [id, fetchLetters, toast]);

  const combinedContextDocuments = useMemo(() => {
    const registry = new Map<string, DocumentSummarySource>();
    for (const doc of selectedDocs) {
      if (!doc?.id) continue;
      registry.set(doc.id, doc);
    }
    const additionalCollections = [
      strategyPlanData?.context_documents,
      viewLetter?.contextDocuments,
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
  }, [selectedDocs, strategyPlanData?.context_documents, viewLetter?.contextDocuments]);

  const runContextIds = useMemo(() => {
    const fromRun = strategyPlanData?.context_document_ids ?? [];
    if (fromRun.length > 0) return fromRun.map(String);
    return (viewLetter?.contextDocumentIds ?? []).map(String);
  }, [strategyPlanData?.context_document_ids, viewLetter?.contextDocumentIds]);

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
    if (!strategyPlanData?.generated_at) return undefined;
    return `Updated ${formatDateTime(strategyPlanData.generated_at)}`;
  }, [strategyPlanData?.generated_at]);

  const conversationThread = useMemo(
    () =>
      ((viewLetter?.graphThread ?? []) as LanggraphGraphThreadNode[]) ?? [],
    [viewLetter?.graphThread]
  );

  const handleContextSelection = useCallback(
    (ids: string[], docs: ContextDocumentSummary[]) => {
      setSelectedDocIds(ids);
      setSelectedDocs(docs);
    },
    []
  );

  const handleGenerateBackground = useCallback(async () => {
    if (!id || !viewLetter) return;
    try {
      setRunningBackground(true);
      const response = await runDraftingWorkflow(id, {
        mode: "background",
        draft_type: "reply",
        letter_category: "general",
        role: selectedRole,
        recipient_focus: selectedRole === "engineer" ? engineerRecipient : undefined,
        subject: viewLetter.subject,
        recipient: viewLetter.recipient,
        requirements: viewLetter.content,
        points: summaryPoints.length > 0 ? summaryPoints.join("\n") : undefined,
        document_ids: selectedDocIds,
      });

      setPlanDraft(response.plan ?? planDraft);
      const background = backgroundItemsFromDraftRun(response);
      setBackgroundItems(background);
      setStrategyPlanData(strategyPlanFromDraftRun(response, viewLetter, background));

      await fetchLetters();
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
    runDraftingWorkflow,
    engineerRecipient,
    planDraft,
    selectedRole,
    viewLetter,
    summaryPoints,
    selectedDocIds,
    fetchLetters,
    toast,
  ]);

  const handleGeneratePlan = useCallback(async () => {
    if (!id || !viewLetter) return;
    if (!hasContexts) {
      toast({
        title: "Contexts required",
        description: "Generate three-way contexts before requesting a plan.",
        variant: "destructive",
      });
      return;
    }
    try {
      const response = await preparePlan(id, {
        mode: "strategy",
        draft_type: "reply",
        letter_category: "general",
        role: selectedRole,
        recipient_focus: selectedRole === "engineer" ? engineerRecipient : undefined,
        subject: viewLetter.subject,
        recipient: viewLetter.recipient,
        requirements: [
          aggregatedContexts.contractor,
          aggregatedContexts.engineer,
          aggregatedContexts.employer,
          viewLetter.content,
        ]
          .filter(Boolean)
          .join("\n\n"),
        points: summaryPoints.length > 0 ? summaryPoints.join("\n") : undefined,
        document_ids: selectedDocIds,
      });

      const background = backgroundItemsFromDraftRun(response);
      const planResponse = strategyPlanFromDraftRun(response, viewLetter, background);
      setPlanDraft(planResponse.plan ?? "");
      setSummaryPoints(planResponse.summary_points ?? []);
      setBackgroundItems(background);
      setStrategyPlanData(planResponse);

      await fetchLetters();
      toast({
        title: "Plan generated",
        description: "Drafting engine produced a new strategic plan for this letter.",
      });
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ??
        error?.message ??
        "Drafting engine planning failed.";
      toast({
        title: "Unable to generate plan",
        description,
        variant: "destructive",
      });
    }
  }, [
    aggregatedContexts.contractor,
    aggregatedContexts.engineer,
    aggregatedContexts.employer,
    engineerRecipient,
    fetchLetters,
    preparePlan,
    hasContexts,
    id,
    selectedDocIds,
    selectedRole,
    summaryPoints,
    toast,
    viewLetter,
  ]);

  const handleSavePlan = useCallback(async () => {
    if (!id) return;
    setSaving(true);
    try {
      const outlinePayload = strategyPlanData
        ? {
            tone_approach: strategyPlanData.tone_approach,
            content_structure: strategyPlanData.content_structure,
            specific_responses: strategyPlanData.specific_responses,
            risk_mitigation: strategyPlanData.risk_mitigation,
            desired_outcome: strategyPlanData.desired_outcome,
          }
        : viewLetter?.strategicOutline;
      await handleLetterUpdate(id, {
        strategy_plan: planDraft,
        strategic_outline: outlinePayload ?? undefined,
        summary_points: summaryPoints,
        strategy_role: selectedRole,
        strategy_recipient:
          selectedRole === "engineer" ? engineerRecipient : undefined,
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
  }, [
    handleLetterUpdate,
    fetchLetters,
    id,
    planDraft,
    strategyPlanData,
    selectedRole,
    engineerRecipient,
    summaryPoints,
    toast,
    viewLetter?.strategicOutline,
  ]);

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
    if (!hasContexts) {
      toast({
        title: "Contexts required",
        description: "Generate contractor/engineer/employer contexts first.",
        variant: "destructive",
      });
      return;
    }
    if (isEditing) {
      await handleSavePlan();
    } else if (id) {
      await handleLetterUpdate(id, {
        strategy_plan: planDraft,
        summary_points: summaryPoints,
        strategy_role: selectedRole,
        strategy_recipient:
          selectedRole === "engineer" ? engineerRecipient : undefined,
      });
    }
    if (id) {
      if (strategyPlanData?.run_id) {
        await acceptPlan(id, strategyPlanData.run_id);
      } else {
        await handleLetterUpdate(id, {
          strategy_plan_approved_by: currentUser?.id ?? currentUser?.email,
          strategy_plan_approved_at: new Date().toISOString(),
        } as any);
      }
      await handleLetterUpdate(id, { status: "Draft" });
      await fetchLetters();
    }
    toast({
      title: "Strategy approved",
      description: "Plan locked in. Continue drafting the letter.",
    });
    navigate(`/letters/${id}/draft`);
  }, [
    engineerRecipient,
    acceptPlan,
    currentUser?.email,
    currentUser?.id,
    fetchLetters,
    handleLetterUpdate,
    handleSavePlan,
    hasContexts,
    id,
    isEditing,
    navigate,
    planDraft,
    selectedRole,
    summaryPoints,
    strategyPlanData?.run_id,
    toast,
  ]);

  const planHasContent = planDraft.trim().length > 0;
  const trace = useMemo(
    () =>
      deriveTraceFromSources(
        strategyPlanData?.trace,
        viewLetter?.strategyGraphTrace,
        viewLetter?.draftTrace
      ),
    [strategyPlanData, viewLetter]
  );

  if (!viewLetter) {
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
  const planSummary =
    summaryPoints.length > 0
      ? summaryPoints
      : strategyPlanData?.summary_points ?? [];
  const planStatus =
    strategyPlanData?.status ??
    viewLetter.strategyGraphStatus ??
    viewLetter.graphStatus;

  return (
    <div className="container mx-auto p-12 max-w-screen-2xl space-y-6">
      <h1 className="text-2xl font-bold">Letter Strategic Plan</h1>
      <div>
        <Button variant="ghost" onClick={() => navigate("/letters")}>
          <ArrowLeft className="mr-2 h-4 w-4" />
          Back to Letters
        </Button>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-start justify-between gap-6">
          <div>
            <CardTitle className="text-2xl">{viewLetter.title}</CardTitle>
            <div className="mt-2 space-y-1 text-sm text-muted-foreground">
              <p>
                <strong>To:</strong> {viewLetter.recipient}
              </p>
              <p>
                <strong>Subject:</strong> {viewLetter.subject}
              </p>
            </div>
          </div>
          <div className="flex flex-col items-end gap-2">
            <Badge variant="neutral">Strategic Planning</Badge>
            <GraphStatusBadge
              status={planStatus}
            />
          </div>
        </CardHeader>
      </Card>

      <RoleSelector
        role={selectedRole}
        onRoleChange={handleRoleChange}
        engineerRecipient={engineerRecipient}
        onEngineerRecipientChange={handleEngineerRecipientChange}
      />

      <Card>
        <CardHeader className="flex flex-col gap-4 md:flex-row md:items-center md:justify-between">
          <div>
            <CardTitle className="text-lg font-semibold">
              Three-Way Context
            </CardTitle>
            <p className="text-sm text-muted-foreground">
              Consolidated correspondence for contractor, engineer, and employer perspectives.
            </p>
          </div>
          <Button
            variant="outline"
            onClick={handleGenerateContexts}
            disabled={!id || contextLoading}
            className="gap-2"
          >
            {contextLoading ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Generating...
              </>
            ) : (
              <>
                <Sparkles className="h-4 w-4" />
                Generate Contexts
              </>
            )}
          </Button>
        </CardHeader>
        <CardContent>
          <ContextPreviewTabs
            contractor={aggregatedContexts.contractor}
            engineer={aggregatedContexts.engineer}
            employer={aggregatedContexts.employer}
          />
        </CardContent>
      </Card>

      <div className="grid gap-6 lg:grid-cols-2">
        <LinkedDocumentSelector
          letterId={id ?? ""}
          organizationId={viewLetter.organizationId}
          projectId={viewLetter.projectId}
          activeLetterCode={viewLetter.letterNo}
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
              disabled={runningBackground || strategyGenerating}
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
        <CardHeader className="flex flex-col gap-4 md:flex-row md:items-start md:justify-between">
          <div>
            <CardTitle className="flex items-center gap-2 text-xl font-semibold">
              <Sparkles className="h-5 w-5 text-primary" />
              Strategic Plan
            </CardTitle>
            {strategyPlanData?.generated_at && (
              <p className="text-sm text-muted-foreground">
                Last generated {formatDateTime(strategyPlanData.generated_at)}
              </p>
            )}
          </div>
          <div className="flex flex-wrap gap-2">
            <Button
              onClick={handleGeneratePlan}
              disabled={
                strategyGenerating || !id || !hasContexts || contextLoading
              }
            >
              {strategyGenerating ? (
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
          {!planHasContent && !strategyGenerating ? (
            <div className="text-center py-12">
              <Sparkles className="h-16 w-16 mx-auto text-muted-foreground mb-4" />
              <h3 className="text-lg font-semibold mb-2">
                No Strategic Plan Yet
              </h3>
              <p className="text-muted-foreground mb-4">
                Generate a drafting plan to guide the workflow.
              </p>
              <Button
                onClick={handleGeneratePlan}
                disabled={!id || !hasContexts || contextLoading}
              >
                <Sparkles className="mr-2 h-4 w-4" />
                Generate Plan
              </Button>
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
                        setPlanDraft(
                        strategyPlanData?.plan ?? viewLetter.strategicPlan ?? ""
                        );
                        setIsEditing(false);
                      }}
                    >
                      Cancel
                    </Button>
                  </div>
                </>
              ) : (
                <>
                  {strategyPlanData ? (
                    <>
                      <StrategyPlanDisplay
                        data={strategyPlanData}
                        onEdit={() => setIsEditing(true)}
                        onApprove={handleProceedToDraft}
                        loading={strategyGenerating || saving}
                      />
                      <PlanViewer
                        plan={planDraft}
                        summaryPoints={planSummary}
                        documents={combinedContextDocuments}
                        trace={trace}
                      />
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
                            disabled={strategyGenerating}
                          >
                            <Sparkles className="mr-2 h-4 w-4" />
                            Regenerate Plan
                          </Button>
                          <Button
                            onClick={handleProceedToDraft}
                            className="gap-2"
                          >
                            <CheckCircle className="h-4 w-4" />
                            Approve &amp; Proceed
                          </Button>
                        </div>
                      </div>
                    </>
                  )}
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
