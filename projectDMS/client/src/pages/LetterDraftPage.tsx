import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { ArrowLeft, CheckCircle2, FileDown, RefreshCw, Send, Sparkles } from "lucide-react";
import { useLetterWorkflow } from "@/hooks/useLetterWorkflow";
import { useLetterGraphRuns } from "@/hooks/useLetterGraphRuns";
import { useLanggraphDraft } from "@/hooks/useLanggraphDraft";
import { useLetterDrafting } from "@/hooks/useLetterDrafting";
import { LANGGRAPH_ENABLED } from "@/config/features";
import LetterDraftEditor from "@/components/letter-workflow/LetterDraftEditor";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useToast } from "@/hooks/use-toast";
import PlanViewer from "@/components/langgraph/PlanViewer";
import GraphStatusBadge from "@/components/langgraph/GraphStatusBadge";
import { LinkedDocumentSelector } from "@/components/letter-workflow/LinkedDocumentSelector";
import BackgroundSummary from "@/components/letter-workflow/BackgroundSummary";
import DraftSourcesPanel from "@/components/letter-workflow/DraftSourcesPanel";
import { DraftEvidencePanel } from "@/components/letter-workflow/DraftEvidencePanel";
import ProbingQuestionsCard from "@/components/letter-workflow/ProbingQuestionsCard";
import LegalRiskPanel from "@/components/letter-workflow/LegalRiskPanel";
import ApprovalChainCard from "@/components/letter-workflow/ApprovalChainCard";
import LockedParagraphsPanel from "@/components/letter-workflow/LockedParagraphsPanel";
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
import type {
  DraftRunResponse,
  DraftType,
  LetterCategory,
  SectionEditAction,
  SourceEvidence,
} from "@/types/letterDrafting";

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

  const { loading: langgraphLoading } = useLanggraphDraft();
  const {
    run: runDraftingWorkflow,
    generateDraft,
    reviseRun,
    validateRun,
    critiqueRun,
    approveRun,
    approveStage,
    provideUserDirection,
    resumeWorkflowRun,
    freezeSections,
    reviseSections,
    exportRun,
    issueRun,
    loading: draftingV2Loading,
  } = useLetterDrafting();

  const letter = useMemo(
    () => letters.find((entry) => entry.id === id),
    [letters, id]
  );

  const uiLetter: UILetter | null = useMemo(
    () => (letter ? mapLetterToUi(letter, users) : null),
    [letter, users]
  );

  const [v2DraftBody, setV2DraftBody] = useState<string | null>(null);

  const editorLetter = useMemo(() => {
    if (!uiLetter) return null;
    const content =
      v2DraftBody ?? graphRun?.draft?.body ?? uiLetter.draftBody ?? uiLetter.content;
    return {
      ...uiLetter,
      content,
    };
  }, [uiLetter, graphRun, v2DraftBody]);

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
  const [draftType, setDraftType] = useState<DraftType>("reply");
  const [letterCategory, setLetterCategory] = useState<LetterCategory>("general");
  const [draftPurpose, setDraftPurpose] = useState("");
  const [requiredAction, setRequiredAction] = useState("");
  const [triggerEvent, setTriggerEvent] = useState("");
  const [v2Run, setV2Run] = useState<DraftRunResponse | null>(null);

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
    if (uiLetter.status === "Strategy") {
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

  // Thread codes the user has not explicitly linked — sent as
  // `exclude_letter_codes` on both the background and draft runs.
  const excludeLetterCodes = useMemo(
    () =>
      graphThreadCodes.filter(
        (code) => code && !linkedLetterCodes.includes(code)
      ),
    [graphThreadCodes, linkedLetterCodes]
  );

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
      (v2Run?.sources as LanggraphDraftSource[] | undefined) ??
      (graphRun?.sources as LanggraphDraftSource[] | undefined) ??
      (uiLetter?.draftSources as LanggraphDraftSource[] | undefined) ??
      [],
    [graphRun?.sources, uiLetter?.draftSources, v2Run?.sources]
  );

  const reviewerFindings = useMemo(
    () =>
      (v2Run?.validation_report?.findings as LanggraphDraftReviewFinding[] | undefined) ??
      (graphRun?.reviewer_findings as LanggraphDraftReviewFinding[] | undefined) ??
      (uiLetter?.reviewerFindings as LanggraphDraftReviewFinding[] | undefined) ??
      [],
    [graphRun?.reviewer_findings, uiLetter?.reviewerFindings, v2Run?.validation_report?.findings]
  );

  const reviewerBlocking =
    v2Run?.validation_report?.blocking ??
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

  const hasSavedStrategicPlan = Boolean(uiLetter?.strategicPlan?.trim());
  const hasApprovedStrategicPlan =
    hasSavedStrategicPlan &&
    Boolean(
      uiLetter?.strategyPlanApprovedAt ||
        ["Draft", "Review", "Approval", "Completed"].includes(uiLetter?.status ?? "")
    );

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
      const response = await runDraftingWorkflow(id, {
        mode: "background",
        draft_type: draftType,
        letter_category: letterCategory,
        role: (uiLetter as any).strategyRole ?? "contractor",
        subject: uiLetter.subject,
        recipient: uiLetter.recipient,
        requirements: editorLetter.content,
        points: summaryLines.length > 0 ? summaryLines.join("\n") : undefined,
        document_ids: selectedDocIds,
        include_letter_codes: linkedLetterCodes,
        exclude_letter_codes: excludeLetterCodes,
      });
      setV2Run(response);
      setBackgroundItems(backgroundItemsFromDraftRun(response));
      await fetchLetters();
      toast({
        title: "Background generated",
        description: "AI background summary refreshed successfully.",
      });
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ??
        error?.message ??
        "Drafting engine background generation failed.";
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
    runDraftingWorkflow,
    graphRun,
    uiLetter,
    editorLetter,
    selectedDocIds,
    linkedLetterCodes,
    excludeLetterCodes,
    draftType,
    letterCategory,
    fetchLetters,
    toast,
  ]);

  const handleRunDraft = useCallback(async (assistantInstructions?: string) => {
    if (!id || !uiLetter || !editorLetter) return;
    if (!uiLetter.strategicPlan?.trim()) {
      toast({
        title: "Strategic plan required",
        description:
          "Save the strategic plan before generating the response draft.",
        variant: "destructive",
      });
      return;
    }
    if (!hasApprovedStrategicPlan) {
      toast({
        title: "Strategy approval required",
        description:
          "Approve the strategic plan before generating the response draft.",
        variant: "destructive",
      });
      return;
    }
    try {
      const summaryLines =
        graphRun?.summary_points ?? uiLetter.summaryPoints ?? [];
      const currentInstructions = [
        editorLetter.content,
        assistantInstructions ? `Drafter instruction: ${assistantInstructions}` : "",
      ]
        .filter((value) => value && value.trim())
        .join("\n\n");
      const response = await generateDraft(id, {
        draft_type: draftType,
        letter_category: letterCategory,
        role: (uiLetter as any).strategyRole ?? "contractor",
        subject: uiLetter.subject,
        recipient: uiLetter.recipient,
        requirements: currentInstructions,
        purpose: draftPurpose || assistantInstructions || undefined,
        required_action: requiredAction || undefined,
        trigger_event: triggerEvent || undefined,
        points: summaryLines.length > 0 ? summaryLines.join("\n") : undefined,
        document_ids: selectedDocIds,
        incoming_document_id:
          draftType === "reply"
            ? selectedDocIds[0] ?? uiLetter.contextDocumentIds?.[0]
            : undefined,
        incoming_letter_id:
          draftType === "reply" ? uiLetter.reference?.id : undefined,
        plan_override: planOverride || undefined,
        include_letter_codes: linkedLetterCodes,
        exclude_letter_codes: excludeLetterCodes,
      });
      setV2Run(response);
      if (response.draft_artifact?.draft_letter) {
        setV2DraftBody(response.draft_artifact.draft_letter);
      }
      setBackgroundItems(
        (graphRun?.background_summary as LanggraphBackgroundItem[]) ?? []
      );
      await fetchLetters();
      await fetchRun(id);
      toast({
        title:
          response.status === "awaiting_user_direction"
            ? "Direction required"
            : "Draft updated",
        description:
          response.status === "awaiting_user_direction"
            ? "Answer the material questions before drafting can continue."
            : "Structured drafting workflow generated a new draft.",
      });
      return response.draft_artifact?.draft_letter ?? null;
    } catch (error: any) {
      const description =
        error?.response?.data?.detail ??
        error?.message ??
        "Structured drafting workflow failed.";
      toast({
        title: "Unable to generate draft",
        description,
        variant: "destructive",
      });
      throw error;
    }
  }, [
    id,
    uiLetter,
    editorLetter,
    generateDraft,
    graphRun,
    selectedDocIds,
    planOverride,
    linkedLetterCodes,
    excludeLetterCodes,
    draftType,
    letterCategory,
    draftPurpose,
    requiredAction,
    triggerEvent,
    fetchLetters,
    fetchRun,
    hasApprovedStrategicPlan,
    toast,
  ]);

  const handleReviseDraft = useCallback(
    async (revisionAction: "make_firmer" | "make_more_polite" | "make_detailed") => {
      if (!id || !v2Run?.run_id) return;
      try {
        const response = await reviseRun(id, v2Run.run_id, {
          revision_action: revisionAction,
        });
        setV2Run(response);
        setV2DraftBody(response.draft_artifact?.draft_letter ?? null);
        toast({
          title: "Draft revised",
          description: "A revised draft run was created.",
        });
      } catch (error: any) {
        toast({
          title: "Unable to revise draft",
          description: error?.message ?? "Draft revision failed.",
          variant: "destructive",
        });
      }
    },
    [id, reviseRun, toast, v2Run]
  );

  const handleQualityAction = useCallback(
    async (action: "validate" | "critique") => {
      if (!id || !v2Run?.run_id) return;
      try {
        const response =
          action === "validate"
            ? await validateRun(id, v2Run.run_id)
            : await critiqueRun(id, v2Run.run_id);
        setV2Run(response);
        toast({
          title: action === "validate" ? "Draft validated" : "Draft critiqued",
          description:
            action === "validate"
              ? "Source and clause validation was refreshed."
              : "Contractual red-flag critique was refreshed.",
        });
      } catch (error: any) {
        toast({
          title:
            action === "validate"
              ? "Unable to validate draft"
              : "Unable to critique draft",
          description: error?.message ?? "Draft quality check failed.",
          variant: "destructive",
        });
      }
    },
    [critiqueRun, id, toast, validateRun, v2Run]
  );

  const handleLifecycleAction = useCallback(
    async (action: "approve" | "export" | "issue") => {
      if (!id || !v2Run?.run_id) return;
      const lifecycle: Record<
        "approve" | "export" | "issue",
        {
          run: (letterId: string, runId: string) => Promise<DraftRunResponse>;
          title: string;
        }
      > = {
        approve: { run: approveRun, title: "Draft approved" },
        export: { run: exportRun, title: "Draft exported" },
        issue: { run: issueRun, title: "Draft issued" },
      };
      try {
        const { run, title } = lifecycle[action];
        const response = await run(id, v2Run.run_id);
        setV2Run(response);
        if (response.draft_artifact?.draft_letter) {
          setV2DraftBody(response.draft_artifact.draft_letter);
        }
        await fetchLetters();
        toast({
          title,
          description: "Draft run status was updated.",
        });
      } catch (error: any) {
        toast({
          title: "Unable to update draft",
          description: error?.message ?? "Draft workflow action failed.",
          variant: "destructive",
        });
      }
    },
    [approveRun, exportRun, fetchLetters, id, issueRun, toast, v2Run]
  );

  const handleApproveStage = useCallback(
    async (stage: "drafter" | "reviewer" | "final", comment?: string) => {
      if (!id || !v2Run?.run_id) return;
      try {
        const response = await approveStage(id, v2Run.run_id, { stage, comment });
        setV2Run(response);
        await fetchLetters();
        toast({
          title: `${stage.charAt(0).toUpperCase() + stage.slice(1)} approval recorded`,
          description:
            stage === "final"
              ? "Approval chain complete — draft approved and version locked."
              : "Next approval stage is now pending.",
        });
      } catch (error: any) {
        toast({
          title: "Approval failed",
          description: error?.message ?? "Approval stage was rejected.",
          variant: "destructive",
        });
      }
    },
    [approveStage, fetchLetters, id, toast, v2Run]
  );

  const handleUserDirection = useCallback(
    async (answers: { question_id?: string; answer: string }[], directions?: string) => {
      if (!id || !v2Run?.run_id) return;
      try {
        const response = v2Run.engine === "langgraph_v3"
          ? await resumeWorkflowRun(id, v2Run.run_id, {
              answers: answers.map((answer) => ({
                ...answer,
                question_version: v2Run.probing_questions?.find(
                  (question) => question.question_id === answer.question_id
                )?.question_version,
              })),
              directions,
              expected_state_version: v2Run.state_version ?? 0,
            })
          : await provideUserDirection(id, v2Run.run_id, { answers, directions });
        setV2Run(response);
        toast({
          title: "Direction recorded",
          description: "Your line of action will be carried into the next strategy/draft run.",
        });
      } catch (error: any) {
        toast({
          title: "Unable to record direction",
          description: error?.message ?? "User direction failed.",
          variant: "destructive",
        });
      }
    },
    [id, provideUserDirection, resumeWorkflowRun, toast, v2Run]
  );

  const handleConfirmV3Strategy = useCallback(async () => {
    if (!id || !v2Run?.run_id || v2Run.engine !== "langgraph_v3") return;
    try {
      const response = await resumeWorkflowRun(id, v2Run.run_id, {
        strategy_approved: true,
        expected_state_version: v2Run.state_version ?? 0,
      });
      setV2Run(response);
      setV2DraftBody(response.draft_artifact?.draft_letter ?? null);
      toast({ title: "Strategy confirmed", description: "Draft generation resumed." });
    } catch (error: any) {
      toast({
        title: "Unable to confirm strategy",
        description: error?.message ?? "Refresh the workflow state and try again.",
        variant: "destructive",
      });
    }
  }, [id, resumeWorkflowRun, toast, v2Run]);

  const handleFreezeSections = useCallback(
    async (sectionIndices: number[], expectedDraftHash?: string) => {
      if (!id || !v2Run?.run_id) return;
      try {
        const response = await freezeSections(id, v2Run.run_id, {
          section_indices: sectionIndices,
          expected_draft_hash: expectedDraftHash,
        });
        setV2Run(response);
        toast({
          title: "Frozen sections saved",
          description: `${sectionIndices.length} section(s) will be preserved exactly.`,
        });
      } catch (error: any) {
        toast({
          title: "Unable to freeze sections",
          description: error?.message ?? "Freeze sections failed.",
          variant: "destructive",
        });
      }
    },
    [freezeSections, id, toast, v2Run]
  );

  const handleReviseSections = useCallback(
    async (
      sectionIndices: number[],
      action: SectionEditAction,
      expectedDraftHash?: string,
    ) => {
      if (!id || !v2Run?.run_id) return;
      try {
        const response = await reviseSections(id, v2Run.run_id, {
          section_indices: sectionIndices,
          action,
          expected_draft_hash: expectedDraftHash,
        });
        setV2Run(response);
        setV2DraftBody(response.draft_artifact?.draft_letter ?? null);
        toast({
          title: "Selected sections revised",
          description: "Frozen and unselected content was preserved exactly.",
        });
      } catch (error: any) {
        toast({
          title: "Unable to revise selected sections",
          description: error?.message ?? "Scoped section revision failed.",
          variant: "destructive",
        });
      }
    },
    [id, reviseSections, toast, v2Run]
  );

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
            draft_run_id: v2Run?.run_id,
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
      v2Run,
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
      <h1 className="text-2xl font-bold">Letter Draft</h1>
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

      {!hasSavedStrategicPlan && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-700 mt-0.5" />
            <div>
              <p className="font-semibold text-amber-900">
                Strategic plan required before AI drafting
              </p>
              <p className="text-amber-900/80">
                This draft page is available, but draft generation is blocked until the plan is saved.
              </p>
            </div>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => navigate(`/letters/${id}/strategy`)}
          >
            Open Strategy Plan
          </Button>
        </div>
      )}

      {hasSavedStrategicPlan && !hasApprovedStrategicPlan && (
        <div className="rounded-md border border-amber-300 bg-amber-50 p-3 text-sm flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-start gap-2">
            <AlertTriangle className="h-4 w-4 text-amber-700 mt-0.5" />
            <div>
              <p className="font-semibold text-amber-900">
                Strategy approval required before AI drafting
              </p>
              <p className="text-amber-900/80">
                Return to the strategy page and approve the saved plan before generating the response draft.
              </p>
            </div>
          </div>
          <Button
            size="sm"
            variant="outline"
            onClick={() => navigate(`/letters/${id}/strategy`)}
          >
            Approve Strategy
          </Button>
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
              disabled={runningBackground || draftingV2Loading || (LANGGRAPH_ENABLED && langgraphLoading)}
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
            Drafting Engine Strategic Plan
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
              onClick={() => handleRunDraft()}
              disabled={!hasApprovedStrategicPlan || draftingV2Loading || (LANGGRAPH_ENABLED && langgraphLoading)}
              className="gap-2"
            >
              <Sparkles
                className={`h-4 w-4 ${
                  draftingV2Loading || (LANGGRAPH_ENABLED && langgraphLoading) ? "animate-spin" : ""
                }`}
              />
              {draftingV2Loading || (LANGGRAPH_ENABLED && langgraphLoading)
                ? "Generating..."
                : "Regenerate Draft"}
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-3">
            <label className="space-y-1 text-sm">
              <span className="font-medium">Draft type</span>
              <select
                value={draftType}
                onChange={(event) => setDraftType(event.target.value as DraftType)}
                className="w-full rounded-md border bg-background px-3 py-2"
              >
                <option value="reply">Reply</option>
                <option value="fresh">Fresh</option>
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Category</span>
              <select
                value={letterCategory}
                onChange={(event) =>
                  setLetterCategory(event.target.value as LetterCategory)
                }
                className="w-full rounded-md border bg-background px-3 py-2"
              >
                <option value="general">General</option>
                <option value="claim_reply">Claim reply</option>
                <option value="eot_reply">EOT reply</option>
                <option value="variation">Variation</option>
                <option value="payment_ipc">Payment / IPC</option>
                <option value="advance_recovery">Advance recovery</option>
                <option value="completion">Completion</option>
                <option value="ncr_quality">NCR / quality</option>
                <option value="delay_progress">Delay / progress</option>
                <option value="records_request">Records request</option>
                <option value="dispute">Dispute</option>
              </select>
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Trigger event</span>
              <input
                value={triggerEvent}
                onChange={(event) => setTriggerEvent(event.target.value)}
                placeholder="Event, notice, or issue"
                className="w-full rounded-md border bg-background px-3 py-2"
              />
            </label>
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="space-y-1 text-sm">
              <span className="font-medium">Purpose</span>
              <input
                value={draftPurpose}
                onChange={(event) => setDraftPurpose(event.target.value)}
                placeholder="What this letter must achieve"
                className="w-full rounded-md border bg-background px-3 py-2"
              />
            </label>
            <label className="space-y-1 text-sm">
              <span className="font-medium">Required action</span>
              <input
                value={requiredAction}
                onChange={(event) => setRequiredAction(event.target.value)}
                placeholder="Action expected from the recipient"
                className="w-full rounded-md border bg-background px-3 py-2"
              />
            </label>
          </div>
          {v2Run && (
            <div className="rounded-md border p-3 text-sm">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div>
                  <p className="font-medium">
                    V2 run: {v2Run.status} ({v2Run.run_id.slice(0, 8)})
                  </p>
                  <p className="text-muted-foreground">
                    {v2Run.validation_report?.findings?.length ?? 0} validation finding(s)
                  </p>
                </div>
                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleReviseDraft("make_firmer")}
                    disabled={draftingV2Loading}
                  >
                    Firmer
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleReviseDraft("make_more_polite")}
                    disabled={draftingV2Loading}
                  >
                    Politer
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    onClick={() => handleReviseDraft("make_detailed")}
                    disabled={draftingV2Loading}
                  >
                    Detailed
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1"
                    onClick={() => handleQualityAction("validate")}
                    disabled={draftingV2Loading}
                  >
                    <CheckCircle2 className="h-4 w-4" />
                    Validate
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1"
                    onClick={() => handleQualityAction("critique")}
                    disabled={draftingV2Loading}
                  >
                    <AlertTriangle className="h-4 w-4" />
                    Critique
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1"
                    onClick={() => handleLifecycleAction("export")}
                    disabled={draftingV2Loading}
                  >
                    <FileDown className="h-4 w-4" />
                    Export
                  </Button>
                  <Button
                    size="sm"
                    variant="outline"
                    className="gap-1"
                    onClick={() => handleLifecycleAction("issue")}
                    disabled={draftingV2Loading}
                  >
                    <Send className="h-4 w-4" />
                    Issue
                  </Button>
                </div>
              </div>
              {(v2Run.validation_report?.findings ?? []).length > 0 && (
                <div className="mt-3 space-y-1 text-xs text-muted-foreground">
                  {(v2Run.validation_report?.findings ?? []).slice(0, 4).map((finding) => (
                    <p key={`${finding.code}-${finding.message}`}>
                      {finding.level.toUpperCase()}: {finding.message}
                    </p>
                  ))}
                </div>
              )}
            </div>
          )}
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
              onGenerateAiDraft={handleRunDraft}
              aiDraftDisabled={!hasApprovedStrategicPlan || draftingV2Loading || (LANGGRAPH_ENABLED && langgraphLoading)}
              aiDraftDisabledReason={
                !hasApprovedStrategicPlan
                  ? "Approve the saved strategy plan before AI drafting."
                  : undefined
              }
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

          <DraftEvidencePanel letterId={id} runId={v2Run?.run_id} run={v2Run} />

          {v2Run && (
            <>
              <ProbingQuestionsCard
                questions={v2Run.probing_questions ?? []}
                existingDirections={v2Run.user_directions ?? []}
                loading={draftingV2Loading}
                onSubmit={handleUserDirection}
              />
              {v2Run.engine === "langgraph_v3" && v2Run.next_action === "confirm_strategy" && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-sm">Confirm drafting strategy</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <Button onClick={handleConfirmV3Strategy} disabled={draftingV2Loading}>
                      Confirm strategy and generate draft
                    </Button>
                  </CardContent>
                </Card>
              )}
              <LegalRiskPanel report={v2Run.legal_risk_report} />
              <LockedParagraphsPanel
                run={v2Run}
                loading={draftingV2Loading}
                onFreeze={handleFreezeSections}
                onRevise={handleReviseSections}
              />
              <ApprovalChainCard
                approvals={v2Run.approvals ?? []}
                disabled={
                  draftingV2Loading ||
                  v2Run.status === "blocked" ||
                  Boolean(v2Run.validation_report?.blocking)
                }
                onApproveStage={handleApproveStage}
              />
            </>
          )}

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
