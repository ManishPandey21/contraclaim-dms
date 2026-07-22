import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useLocation, useNavigate, useParams } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  Archive,
  CheckCircle2,
  Download,
  FilePlus2,
  Landmark,
  Loader2,
  Plus,
  RefreshCw,
  ShieldAlert,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import { enhancedApi } from "@/services/enhanced-api";
import {
  ArbitrationCase,
  BundleExport,
  BundleExportFormat,
  CaseDashboard,
  FilingBundleManifest,
  MATRIX_DEFINITIONS,
  MatrixRow,
  MatrixSlug,
  ReadinessResponse,
  approveArbitrationReadiness,
  createArbitrationCase,
  createMatrixRow,
  downloadCaseBundleDocx,
  downloadCaseBundlePdf,
  downloadCaseBundleZip,
  downloadQueuedCaseBundleExport,
  getArbitrationCase,
  getArbitrationCaseDashboard,
  getArbitrationReadiness,
  getCaseBundleExport,
  getFilingBundleManifest,
  listArbitrationCases,
  listMatrixRows,
  queueArbitrationAgent,
  queueCaseBundleExport,
  reviewMatrixRow,
  runArbitrationAgent,
} from "@/services/arbitration-cases-api";
import {
  ArbitrationDraftType,
  ArbitrationWorkflowState,
  approveArbitrationWorkflowGate,
  cancelArbitrationWorkflow,
  createArbitrationWorkflow,
  createArbitrationDraft,
  getArbitrationWorkflowEvents,
  getArbitrationWorkflowState,
  listArbitrationWorkflows,
  prepareArbitrationDraftFromCase,
  resumeArbitrationWorkflow,
} from "@/services/arbitration-drafting-api";

type ProjectOption = { id?: string; _id?: string; name?: string; organization_id?: string };

interface CaseFormState {
  organization_id: string;
  project_id: string;
  title: string;
  case_reference: string;
  party_perspective: "claimant" | "respondent" | "both" | "neutral";
  tribunal_details: string;
  institutional_rules: string;
  seat: string;
  venue: string;
  language: string;
  governing_law: string;
  arbitration_clause: string;
  case_summary: string;
}

const initialCaseForm: CaseFormState = {
  organization_id: "",
  project_id: "",
  title: "",
  case_reference: "",
  party_perspective: "neutral",
  tribunal_details: "",
  institutional_rules: "",
  seat: "",
  venue: "",
  language: "English",
  governing_law: "",
  arbitration_clause: "",
  case_summary: "",
};

const AGENT_ACTIONS = [
  { type: "orchestrator", label: "Orchestrator" },
  { type: "document-indexing", label: "Documents" },
  { type: "chronology-builder-adapter", label: "Chronology" },
  { type: "clause-interpretation", label: "Clauses" },
  { type: "claim-identification", label: "Claims" },
  { type: "quantum", label: "Quantum" },
  { type: "notice-compliance", label: "Notices" },
  { type: "issue-framing", label: "Issues" },
  { type: "jurisdiction", label: "Jurisdiction" },
  { type: "delay-expert", label: "Expert" },
  { type: "defence-analysis", label: "Defence" },
  { type: "rejoinder-reply", label: "Rejoinder" },
] as const;

const EXHIBIT_PREFIXES = ["C", "R", "J", "CE", "RE", "QE", "DE"];

const MATRIX_FIELDS: Record<
  MatrixSlug,
  Array<{ key: string; label: string; type?: "textarea" | "number" | "select"; options?: string[] }>
> = {
  "document-index": [
    { key: "title", label: "Document title" },
    { key: "source_type", label: "Source type" },
    { key: "source_id", label: "Source id" },
    { key: "document_date", label: "Document date" },
    { key: "document_type", label: "Document type" },
    { key: "exhibit_prefix", label: "Exhibit prefix", type: "select", options: EXHIBIT_PREFIXES },
    { key: "relevance_note", label: "Relevance note", type: "textarea" },
  ],
  "chronology-matrix": [
    { key: "date", label: "Date" },
    { key: "event", label: "Event", type: "textarea" },
    { key: "document_ref", label: "Document ref" },
    { key: "party_responsible", label: "Responsible party" },
    { key: "impact", label: "Impact", type: "textarea" },
  ],
  "clause-matrix": [
    { key: "topic", label: "Topic" },
    { key: "clause_number", label: "Clause number" },
    { key: "clause_text_excerpt", label: "Clause excerpt", type: "textarea" },
    { key: "obligation_or_right", label: "Obligation or right", type: "textarea" },
    { key: "risk", label: "Risk", type: "textarea" },
  ],
  "issue-matrix": [
    { key: "issue_no", label: "Issue no." },
    { key: "issue", label: "Issue", type: "textarea" },
    { key: "issue_type", label: "Issue type" },
    { key: "claimant_position", label: "Claimant position", type: "textarea" },
    { key: "respondent_position", label: "Respondent position", type: "textarea" },
  ],
  "claim-matrix": [
    { key: "claim_no", label: "Claim no." },
    { key: "claim_head", label: "Claim head" },
    { key: "amount_or_days", label: "Amount or days" },
    { key: "facts", label: "Facts", type: "textarea" },
    { key: "causation", label: "Causation", type: "textarea" },
    { key: "relief", label: "Relief", type: "textarea" },
  ],
  "defence-matrix": [
    { key: "source_claim_no", label: "Source claim no." },
    { key: "admission_denial", label: "Admission or denial" },
    { key: "defence", label: "Defence", type: "textarea" },
    { key: "quantum_objection", label: "Quantum objection", type: "textarea" },
    { key: "positive_case", label: "Positive case", type: "textarea" },
  ],
  "counterclaim-matrix": [
    { key: "counterclaim_no", label: "Counterclaim no." },
    { key: "facts", label: "Facts", type: "textarea" },
    { key: "breach", label: "Breach", type: "textarea" },
    { key: "amount_or_days", label: "Amount or days" },
    { key: "relief", label: "Relief", type: "textarea" },
  ],
  "rejoinder-matrix": [
    { key: "source_sod_para", label: "Source SoD para" },
    { key: "nature_of_defence", label: "Nature of defence" },
    { key: "claimant_reply", label: "Claimant reply", type: "textarea" },
    { key: "new_matter", label: "New matter" },
    { key: "permission_required", label: "Permission required" },
    { key: "permission_notes", label: "Permission notes", type: "textarea" },
  ],
  "quantum-annexures": [
    { key: "calculation_id", label: "Calculation id" },
    { key: "calculation_type", label: "Calculation type" },
    { key: "formula", label: "Formula", type: "textarea" },
    { key: "amount", label: "Amount", type: "number" },
    { key: "currency", label: "Currency" },
    { key: "cost_head", label: "Cost head (delay/cost link)" },
    { key: "critical_path_days", label: "Critical path days", type: "number" },
  ],
  "notice-compliance": [
    { key: "notice_ref", label: "Notice ref" },
    { key: "notice_date", label: "Notice date" },
    { key: "requirement", label: "Requirement", type: "textarea" },
    { key: "compliance_status", label: "Compliance status" },
    { key: "risk_note", label: "Risk note", type: "textarea" },
  ],
  "jurisdiction-matrix": [
    { key: "check_type", label: "Check type (limitation / pre_arbitration_step / arbitration_clause_scope)" },
    { key: "step", label: "Pre-arb step" },
    { key: "required", label: "Required (pre-arb step)" },
    { key: "compliance_status", label: "Compliance status" },
    { key: "cause_of_action_date", label: "Cause of action date" },
    { key: "limitation_status", label: "Limitation status" },
    { key: "notes", label: "Notes", type: "textarea" },
  ],
  "expert-alignment": [
    { key: "expert_type", label: "Expert type (delay / quantum / technical / contract)" },
    { key: "claim_no", label: "Claim no." },
    { key: "methodology", label: "Methodology", type: "textarea" },
    { key: "concurrency_addressed", label: "Concurrency addressed (delay)" },
    { key: "verified_amount", label: "Expert-verified amount (quantum)", type: "number" },
    { key: "calculation_match", label: "Calculation match (quantum)" },
    { key: "notes", label: "Notes", type: "textarea" },
  ],
};

const projectId = (project: ProjectOption) => String(project.id || project._id || "");
const pretty = (value?: string | null) => String(value || "").replace(/_/g, " ");

const downloadBlob = (blob: Blob, filename: string) => {
  const href = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = href;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(href);
};

const internalKeys = new Set([
  "_id",
  "case_id",
  "draft_id",
  "organization_id",
  "project_id",
  "contract_id",
  "created_by",
  "created_at",
  "updated_by",
  "updated_at",
  "deleted_at",
  "human_approval_status",
  "review_assignments",
  "review_comments",
  "approval_log",
  "review_required_roles",
  "review_completed_roles",
  "last_review_action",
  "last_reviewed_by",
  "last_reviewed_at",
  "assigned_reviewer_id",
  "approved_by",
  "approved_at",
  "rejected_by",
  "rejected_at",
]);

const usefulRowEntries = (row: MatrixRow) =>
  Object.entries(row)
    .filter(([key, value]) => !internalKeys.has(key) && value !== undefined && value !== null && String(value).trim() !== "")
    .slice(0, 5);

const REVIEW_ROLES = ["legal", "contracts", "technical", "delay", "quantum", "commercial", "reviewer"] as const;

const defaultReviewRole = (matrix: MatrixSlug) => {
  if (matrix === "quantum-annexures") return "quantum";
  if (matrix === "chronology-matrix") return "delay";
  if (matrix === "document-index") return "contracts";
  return "legal";
};

const stringArray = (value: unknown) => (Array.isArray(value) ? value.map(String).filter(Boolean) : []);

const ArbitrationCaseWorkspacePage: React.FC = () => {
  const { caseId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const mode = useMemo(() => {
    const parts = location.pathname.split("/").filter(Boolean);
    return parts[parts.length - 1] || "cases";
  }, [location.pathname]);
  const isNew = mode === "new";
  const section = ["matrices", "readiness", "filing-bundle"].includes(mode) ? mode : "dashboard";

  const [cases, setCases] = useState<ArbitrationCase[]>([]);
  const [caseData, setCaseData] = useState<ArbitrationCase | null>(null);
  const [dashboard, setDashboard] = useState<CaseDashboard | null>(null);
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [manifest, setManifest] = useState<FilingBundleManifest | null>(null);
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [rows, setRows] = useState<MatrixRow[]>([]);
  const [activeMatrix, setActiveMatrix] = useState<MatrixSlug>("document-index");
  const [form, setForm] = useState<CaseFormState>(initialCaseForm);
  const [matrixForm, setMatrixForm] = useState<Record<string, string>>({});
  const [reviewRoles, setReviewRoles] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [runningAgent, setRunningAgent] = useState<string | null>(null);
  const [queueingAgent, setQueueingAgent] = useState<string | null>(null);
  const [agentOptions, setAgentOptions] = useState({
    agent_mode: "deterministic",
    interest_rate: "",
    interest_period_days: "",
    limitation_period_years: "",
    cause_of_action_date: "",
  });
  const [queuedExport, setQueuedExport] = useState<BundleExport | null>(null);
  const [queueingExport, setQueueingExport] = useState<BundleExportFormat | null>(null);
  const [workflow, setWorkflow] = useState<ArbitrationWorkflowState | null>(null);
  const [workflowEvents, setWorkflowEvents] = useState<Array<Record<string, unknown>>>([]);
  const [workflowDraftId, setWorkflowDraftId] = useState("");
  const [workflowDocumentIds, setWorkflowDocumentIds] = useState("");
  const [workflowOpponentDraftId, setWorkflowOpponentDraftId] = useState("");
  const [workflowOpponentVersionId, setWorkflowOpponentVersionId] = useState("");
  const [workflowSecondOpponentDraftId, setWorkflowSecondOpponentDraftId] = useState("");
  const [workflowSecondOpponentVersionId, setWorkflowSecondOpponentVersionId] = useState("");
  const [workflowBusy, setWorkflowBusy] = useState(false);
  const [workflowAnswers, setWorkflowAnswers] = useState<Record<string, string>>({});

  const loadCases = useCallback(async () => {
    setLoading(true);
    try {
      setCases(await listArbitrationCases());
    } catch {
      toast.error("Failed to load arbitration cases");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadWorkspace = useCallback(async () => {
    if (!caseId) return;
    setLoading(true);
    try {
      const [loadedCase, loadedDashboard] = await Promise.all([
        getArbitrationCase(caseId),
        getArbitrationCaseDashboard(caseId),
      ]);
      setCaseData(loadedCase);
      setDashboard(loadedDashboard);
      setReadiness(loadedDashboard.readiness);
      if (section === "filing-bundle") {
        setManifest(await getFilingBundleManifest(caseId));
      }
      if (section === "matrices") {
        setRows(await listMatrixRows(caseId, activeMatrix));
      }
      if (section === "readiness") {
        setReadiness(await getArbitrationReadiness(caseId));
      }
    } catch {
      toast.error("Failed to load arbitration case workspace");
    } finally {
      setLoading(false);
    }
  }, [activeMatrix, caseId, section]);

  useEffect(() => {
    enhancedApi.getProjects().then(setProjects).catch(() => setProjects([]));
  }, []);

  useEffect(() => {
    if (!caseId && !isNew) loadCases();
  }, [caseId, isNew, loadCases]);

  useEffect(() => {
    if (caseId) loadWorkspace();
  }, [caseId, loadWorkspace]);

  useEffect(() => {
    let active = true;
    setWorkflow(null);
    setWorkflowEvents([]);
    if (!caseId) return () => { active = false; };
    listArbitrationWorkflows(caseId, true)
      .then((runs) => {
        if (active && runs.length) setWorkflow(runs[0]);
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [caseId]);

  useEffect(() => {
    setMatrixForm({});
  }, [activeMatrix]);

  useEffect(() => {
    if (!caseId || !workflow?.run_id || ["completed", "cancelled", "failed"].includes(workflow.status)) return;
    const timer = window.setInterval(() => {
      Promise.all([
        getArbitrationWorkflowState(caseId, workflow.run_id),
        getArbitrationWorkflowEvents(caseId, workflow.run_id),
      ]).then(([state, events]) => {
        setWorkflow(state);
        setWorkflowEvents(events);
      }).catch(() => undefined);
    }, 5000);
    return () => window.clearInterval(timer);
  }, [caseId, workflow?.run_id, workflow?.status]);

  useEffect(() => {
    if (!caseId || !workflow?.run_id) {
      setWorkflowEvents([]);
      return;
    }
    getArbitrationWorkflowEvents(caseId, workflow.run_id).then(setWorkflowEvents).catch(() => undefined);
  }, [caseId, workflow?.run_id, workflow?.state_version]);

  const updateCaseForm = (key: keyof CaseFormState, value: string) => {
    setForm((prev) => {
      const next = { ...prev, [key]: value };
      if (key === "project_id") {
        const project = projects.find((item) => projectId(item) === value);
        next.organization_id = project?.organization_id || prev.organization_id;
      }
      return next;
    });
  };

  const saveCase = async () => {
    if (!form.project_id || !form.title.trim()) {
      toast.error("Project and case title are required");
      return;
    }
    setSaving(true);
    try {
      const created = await createArbitrationCase({
        organization_id: form.organization_id || undefined,
        project_id: form.project_id,
        title: form.title,
        case_reference: form.case_reference || undefined,
        party_perspective: form.party_perspective,
        tribunal_details: form.tribunal_details || undefined,
        institutional_rules: form.institutional_rules || undefined,
        seat: form.seat || undefined,
        venue: form.venue || undefined,
        language: form.language || undefined,
        governing_law: form.governing_law || undefined,
        arbitration_clause: form.arbitration_clause || undefined,
        case_summary: form.case_summary || undefined,
      });
      toast.success("Arbitration case created");
      navigate(`/arbitration/cases/${created._id}`);
    } catch {
      toast.error("Unable to create arbitration case");
    } finally {
      setSaving(false);
    }
  };

  const saveMatrixRow = async () => {
    if (!caseId) return;
    setSaving(true);
    try {
      const payload: Record<string, unknown> = {
        ...matrixForm,
      };
      if (activeMatrix === "document-index") {
        payload.source_type = payload.source_type || "document";
        payload.exhibit_prefix = payload.exhibit_prefix || "C";
      }
      await createMatrixRow(caseId, activeMatrix, payload);
      setMatrixForm({});
      setRows(await listMatrixRows(caseId, activeMatrix));
      setDashboard(await getArbitrationCaseDashboard(caseId));
      toast.success("Matrix row saved");
    } catch {
      toast.error("Unable to save matrix row");
    } finally {
      setSaving(false);
    }
  };

  const submitReviewAction = async (
    row: MatrixRow,
    action: "assign" | "comment" | "request_changes" | "approve" | "reject",
  ) => {
    if (!caseId) return;
    const reviewerRole = reviewRoles[row._id] || defaultReviewRole(activeMatrix);
    let comment: string | null = null;
    let reviewerUserId: string | null = null;
    if (action === "assign") {
      reviewerUserId = window.prompt("Reviewer user id")?.trim() || null;
      if (!reviewerUserId) return;
      comment = window.prompt("Review note")?.trim() || null;
    } else if (action === "comment" || action === "request_changes" || action === "reject") {
      comment = window.prompt(action === "comment" ? "Comment" : "Reason")?.trim() || null;
      if (!comment) return;
    }

    try {
      await reviewMatrixRow(caseId, activeMatrix, row._id, {
        action,
        reviewer_role: reviewerRole as "legal" | "contracts" | "technical" | "delay" | "quantum" | "commercial" | "reviewer",
        reviewer_user_id: reviewerUserId,
        required_roles: action === "assign" ? [reviewerRole] : undefined,
        comment,
      });
      const [nextRows, nextDashboard, nextReadiness] = await Promise.all([
        listMatrixRows(caseId, activeMatrix),
        getArbitrationCaseDashboard(caseId),
        getArbitrationReadiness(caseId),
      ]);
      setRows(nextRows);
      setDashboard(nextDashboard);
      setReadiness(nextReadiness);
      toast.success("Review action saved");
    } catch {
      toast.error("Unable to save review action");
    }
  };

  const approveReadiness = async () => {
    if (!caseId) return;
    setSaving(true);
    try {
      await approveArbitrationReadiness(caseId);
      const next = await getArbitrationReadiness(caseId);
      setReadiness(next);
      setDashboard(await getArbitrationCaseDashboard(caseId));
      toast.success("Readiness approved");
    } catch {
      toast.error("Resolve readiness blockers before approval");
    } finally {
      setSaving(false);
    }
  };

  const buildAgentOptions = (): Record<string, unknown> => {
    const options: Record<string, unknown> = {};
    if (agentOptions.agent_mode === "llm") options.agent_mode = "llm";
    if (agentOptions.interest_rate.trim()) options.interest_rate = Number(agentOptions.interest_rate);
    if (agentOptions.interest_period_days.trim()) options.interest_period_days = Number(agentOptions.interest_period_days);
    if (agentOptions.limitation_period_years.trim()) options.limitation_period_years = Number(agentOptions.limitation_period_years);
    if (agentOptions.cause_of_action_date.trim()) options.cause_of_action_date = agentOptions.cause_of_action_date.trim();
    return options;
  };

  const runAgent = async (agentType: string) => {
    if (!caseId) return;
    setRunningAgent(agentType);
    try {
      const run = await runArbitrationAgent(caseId, agentType, { options: buildAgentOptions() });
      const [nextDashboard, nextReadiness] = await Promise.all([
        getArbitrationCaseDashboard(caseId),
        getArbitrationReadiness(caseId),
      ]);
      setDashboard(nextDashboard);
      setReadiness(nextReadiness);
      if (section === "matrices") {
        setRows(await listMatrixRows(caseId, activeMatrix));
      }
      const createdCount = run.created_records?.length || 0;
      toast.success(createdCount ? `Agent created ${createdCount} row${createdCount === 1 ? "" : "s"}` : "Agent run recorded");
    } catch {
      toast.error("Unable to run arbitration agent");
    } finally {
      setRunningAgent(null);
    }
  };

  const queueAgent = async (agentType: string) => {
    if (!caseId) return;
    setQueueingAgent(agentType);
    try {
      const run = await queueArbitrationAgent(caseId, agentType, { options: buildAgentOptions() });
      setDashboard(await getArbitrationCaseDashboard(caseId));
      toast.success(`Queued ${pretty(run.agent_type)} job`);
    } catch {
      toast.error("Unable to queue arbitration agent");
    } finally {
      setQueueingAgent(null);
    }
  };

  const createDraftForCase = async (draftType: ArbitrationDraftType) => {
    if (!caseData) return;
    setSaving(true);
    try {
      const created = await createArbitrationDraft({
        case_id: caseData._id,
        organization_id: caseData.organization_id,
        project_id: caseData.project_id,
        contract_id: caseData.contract_id,
        draft_type: draftType,
        party_role: draftType === "statement_of_defence" || draftType === "counterclaim" ? "respondent" : "claimant",
        dispute_type: "other",
        title: `${caseData.title} - ${pretty(draftType)}`,
        tribunal_details: caseData.tribunal_details,
        arbitration_clause: caseData.arbitration_clause,
        governing_law: caseData.governing_law,
        manual_facts: caseData.case_summary,
      });
      await prepareArbitrationDraftFromCase(created._id);
      toast.success("Draft linked to case");
      navigate(`/arbitration/drafts/${created._id}`);
    } catch {
      toast.error("Unable to create draft from case");
    } finally {
      setSaving(false);
    }
  };

  const selectedWorkflowDraft = (dashboard?.drafts || []).find((item) => String(item._id) === workflowDraftId);

  const startWorkflow = async () => {
    if (!caseId || !selectedWorkflowDraft) {
      toast.error("Select a linked draft first");
      return;
    }
    const pleadingType = String(selectedWorkflowDraft.draft_type) as ArbitrationDraftType;
    if (["statement_of_defence", "rejoinder"].includes(pleadingType) && (!workflowOpponentDraftId || !workflowOpponentVersionId)) {
      toast.error("Select the immutable opponent draft and version");
      return;
    }
    if (pleadingType === "rejoinder" && (!workflowSecondOpponentDraftId || !workflowSecondOpponentVersionId)) {
      toast.error("A Rejoinder must pin both the SoC and SoD immutable versions");
      return;
    }
    setWorkflowBusy(true);
    try {
      const key = `arbitration-${caseId}-${workflowDraftId}-${Date.now()}`;
      setWorkflow(await createArbitrationWorkflow(caseId, {
        draft_id: workflowDraftId,
        pleading_type: pleadingType,
        selected_document_ids: workflowDocumentIds.split(",").map((item) => item.trim()).filter(Boolean),
        opponent_draft_id: workflowOpponentDraftId || undefined,
        opponent_version_id: workflowOpponentVersionId || undefined,
        opponent_pleadings: pleadingType === "rejoinder" ? [
          { draft_id: workflowOpponentDraftId, version_id: workflowOpponentVersionId },
          { draft_id: workflowSecondOpponentDraftId, version_id: workflowSecondOpponentVersionId },
        ] : undefined,
      }, key));
      toast.success("Durable pleading workflow accepted");
    } catch {
      toast.error("Unable to start pleading workflow");
    } finally {
      setWorkflowBusy(false);
    }
  };

  const continueWorkflow = async () => {
    if (!caseId || !workflow) return;
    setWorkflowBusy(true);
    try {
      if (workflow.current_node === "document_selection_gate") {
        setWorkflow(await resumeArbitrationWorkflow(caseId, workflow.run_id, {
          state_version: workflow.state_version,
          gate: "document_selection",
          selected_document_ids: workflowDocumentIds.split(",").map((item) => item.trim()).filter(Boolean),
        }));
      } else if (workflow.current_node === "material_question_gate") {
        setWorkflow(await resumeArbitrationWorkflow(caseId, workflow.run_id, {
          state_version: workflow.state_version,
          gate: "material_question",
          answers: workflowAnswers,
        }));
      } else if (workflow.current_node === "legal_review_gate" && workflow.validation_status !== "passed") {
        setWorkflow(await resumeArbitrationWorkflow(caseId, workflow.run_id, {
          state_version: workflow.state_version,
          gate: "legal_review",
          decision: "refresh_candidate",
        }));
      } else {
        const gateByNode: Record<string, { gate: string; hash?: string | null; role: string }> = {
          matrix_review_gate: { gate: "matrix_review", hash: workflow.matrix_revision_hash, role: "legal_reviewer" },
          readiness_approval_gate: { gate: "readiness", hash: workflow.readiness_artifact_hash, role: "senior_legal_approver" },
          plan_approval_gate: { gate: "plan", hash: workflow.plan_hash, role: "senior_legal_approver" },
          legal_review_gate: { gate: "legal_review", hash: workflow.draft_version_hash, role: "legal_reviewer" },
          draft_approval_gate: { gate: "draft", hash: workflow.draft_version_hash, role: "senior_legal_approver" },
          export_authorization_gate: { gate: "export", hash: workflow.draft_version_hash, role: "export_authorizer" },
        };
        const gate = gateByNode[workflow.current_node];
        if (!gate?.hash) throw new Error("Current gate has no reviewable artifact");
        setWorkflow(await approveArbitrationWorkflowGate(caseId, workflow.run_id, gate.gate, {
          state_version: workflow.state_version, artifact_hash: gate.hash, reviewer_role: gate.role,
        }));
      }
      toast.success("Workflow gate recorded");
    } catch {
      toast.error("Workflow action was rejected; refresh state and review blockers");
    } finally {
      setWorkflowBusy(false);
    }
  };

  const refreshWorkflow = async () => {
    if (!caseId || !workflow) return;
    try {
      setWorkflow(
        workflow.current_node === "matrix_review_gate"
          ? await resumeArbitrationWorkflow(caseId, workflow.run_id, {
              state_version: workflow.state_version, gate: "matrix_review", decision: "refresh",
            })
          : await getArbitrationWorkflowState(caseId, workflow.run_id),
      );
    } catch {
      toast.error("Workflow state changed; reload the case workspace");
    }
  };

  const downloadBundle = async (format: "zip" | "docx" | "pdf") => {
    if (!caseId) return;
    try {
      const blob =
        format === "docx"
          ? await downloadCaseBundleDocx(caseId)
          : format === "pdf"
            ? await downloadCaseBundlePdf(caseId)
            : await downloadCaseBundleZip(caseId);
      downloadBlob(blob, `arbitration-filing-bundle.${format}`);
    } catch {
      toast.error("Unable to download filing bundle");
    }
  };

  const queueBundleExport = async (format: BundleExportFormat) => {
    if (!caseId) return;
    setQueueingExport(format);
    try {
      const exportJob = await queueCaseBundleExport(caseId, format);
      setQueuedExport(exportJob);
      toast.success(`Queued ${format.toUpperCase()} filing bundle export`);
    } catch {
      toast.error("Unable to queue filing bundle export");
    } finally {
      setQueueingExport(null);
    }
  };

  const refreshQueuedExport = async () => {
    if (!caseId || !queuedExport) return;
    try {
      setQueuedExport(await getCaseBundleExport(caseId, queuedExport._id));
    } catch {
      toast.error("Unable to refresh filing bundle export");
    }
  };

  const downloadQueuedExport = async () => {
    if (!caseId || !queuedExport) return;
    try {
      const blob = await downloadQueuedCaseBundleExport(caseId, queuedExport._id);
      downloadBlob(blob, queuedExport.filename || `arbitration-filing-bundle.${queuedExport.format}`);
    } catch {
      toast.error("Queued export is not ready");
    }
  };

  if (!caseId && !isNew) {
    return (
      <div className="space-y-6 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal">Arbitration Case Workspaces</h1>
            <p className="text-sm text-muted-foreground">Matrix-driven preparation before SoC, SoD, counterclaim, and rejoinder drafting.</p>
          </div>
          <Button asChild>
            <Link to="/arbitration/cases/new">
              <Plus className="mr-2 h-4 w-4" />
              New Case
            </Link>
          </Button>
        </div>

        <Card>
          <CardHeader>
            <CardTitle>Case Register</CardTitle>
            <CardDescription>Preparation workspaces with readiness and current status</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Case</TableHead>
                  <TableHead>Project</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Readiness</TableHead>
                  <TableHead className="text-right">Action</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {loading ? (
                  <TableRow>
                    <TableCell colSpan={5}>Loading cases...</TableCell>
                  </TableRow>
                ) : cases.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={5}>No arbitration case workspaces found.</TableCell>
                  </TableRow>
                ) : (
                  cases.map((item) => (
                    <TableRow key={item._id}>
                      <TableCell>
                        <div className="font-medium">{item.title}</div>
                        {item.case_reference && <div className="text-xs text-muted-foreground">{item.case_reference}</div>}
                      </TableCell>
                      <TableCell>{item.project_id}</TableCell>
                      <TableCell>
                        <Badge variant="outline">{pretty(item.status)}</Badge>
                      </TableCell>
                      <TableCell className="min-w-[180px]">
                        <div className="mb-1 text-xs text-muted-foreground">{item.readiness_score || 0}%</div>
                        <Progress value={item.readiness_score || 0} />
                      </TableCell>
                      <TableCell className="text-right">
                        <Button size="sm" variant="outline" asChild>
                          <Link to={`/arbitration/cases/${item._id}`}>Open</Link>
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))
                )}
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (isNew) {
    return (
      <div className="space-y-6 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal">New Arbitration Case</h1>
            <p className="text-sm text-muted-foreground">Create the preparation workspace before drafting pleadings.</p>
          </div>
          <Button variant="outline" asChild>
            <Link to="/arbitration/cases">Case Workspaces</Link>
          </Button>
        </div>

        <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
          <section className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Case Identity</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Project</Label>
                  <Select value={form.project_id} onValueChange={(value) => updateCaseForm("project_id", value)}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select project" />
                    </SelectTrigger>
                    <SelectContent>
                      {projects.map((project) => (
                        <SelectItem key={projectId(project)} value={projectId(project)}>
                          {project.name || projectId(project)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Case reference</Label>
                  <Input value={form.case_reference} onChange={(event) => updateCaseForm("case_reference", event.target.value)} />
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label>Title</Label>
                  <Input value={form.title} onChange={(event) => updateCaseForm("title", event.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Party perspective</Label>
                  <Select value={form.party_perspective} onValueChange={(value) => updateCaseForm("party_perspective", value)}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="claimant">Claimant</SelectItem>
                      <SelectItem value="respondent">Respondent</SelectItem>
                      <SelectItem value="both">Both</SelectItem>
                      <SelectItem value="neutral">Neutral</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Governing law</Label>
                  <Input value={form.governing_law} onChange={(event) => updateCaseForm("governing_law", event.target.value)} />
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Procedure and Summary</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Tribunal details</Label>
                  <Textarea value={form.tribunal_details} onChange={(event) => updateCaseForm("tribunal_details", event.target.value)} rows={3} />
                </div>
                <div className="space-y-2">
                  <Label>Institutional rules</Label>
                  <Textarea value={form.institutional_rules} onChange={(event) => updateCaseForm("institutional_rules", event.target.value)} rows={3} />
                </div>
                <div className="space-y-2">
                  <Label>Seat</Label>
                  <Input value={form.seat} onChange={(event) => updateCaseForm("seat", event.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Venue</Label>
                  <Input value={form.venue} onChange={(event) => updateCaseForm("venue", event.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Language</Label>
                  <Input value={form.language} onChange={(event) => updateCaseForm("language", event.target.value)} />
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label>Arbitration clause</Label>
                  <Textarea value={form.arbitration_clause} onChange={(event) => updateCaseForm("arbitration_clause", event.target.value)} rows={4} />
                </div>
                <div className="space-y-2 md:col-span-2">
                  <Label>Case summary</Label>
                  <Textarea value={form.case_summary} onChange={(event) => updateCaseForm("case_summary", event.target.value)} rows={5} />
                </div>
              </CardContent>
            </Card>
          </section>

          <aside className="space-y-3 rounded-md border bg-background p-4">
            <h2 className="text-base font-medium">Workspace Controls</h2>
            <Button className="w-full" onClick={saveCase} disabled={saving}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FilePlus2 className="mr-2 h-4 w-4" />}
              Create Case
            </Button>
            <p className="text-sm text-muted-foreground">
              Readiness is calculated from approved document, clause, issue, claim, defence, rejoinder, and quantum rows.
            </p>
          </aside>
        </div>
      </div>
    );
  }

  const currentCase = caseData || dashboard?.case;
  const currentReadiness = readiness || dashboard?.readiness;

  return (
    <div className="space-y-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-normal">{currentCase?.title || "Arbitration Case"}</h1>
          <p className="text-sm text-muted-foreground">{currentCase?.case_reference || "Case workspace"}</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" asChild>
            <Link to="/arbitration/cases">Case Workspaces</Link>
          </Button>
          <Button variant="outline" onClick={() => loadWorkspace()} disabled={loading}>
            <RefreshCw className="mr-2 h-4 w-4" />
            Refresh
          </Button>
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Status</CardTitle>
          </CardHeader>
          <CardContent>
            <Badge variant="outline">{pretty(currentCase?.status)}</Badge>
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Readiness</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="mb-2 text-xl font-semibold">{currentReadiness?.readiness_score ?? 0}%</div>
            <Progress value={currentReadiness?.readiness_score ?? 0} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Drafts</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">{dashboard?.drafts?.length || 0}</CardContent>
        </Card>
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Matrices</CardTitle>
          </CardHeader>
          <CardContent className="text-xl font-semibold">
            {Object.values(dashboard?.matrix_counts || {}).reduce((sum, value) => sum + Number(value || 0), 0)}
          </CardContent>
        </Card>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button variant={section === "dashboard" ? "default" : "outline"} asChild>
          <Link to={`/arbitration/cases/${caseId}`}>Dashboard</Link>
        </Button>
        <Button variant={section === "matrices" ? "default" : "outline"} asChild>
          <Link to={`/arbitration/cases/${caseId}/matrices`}>Matrices</Link>
        </Button>
        <Button variant={section === "readiness" ? "default" : "outline"} asChild>
          <Link to={`/arbitration/cases/${caseId}/readiness`}>Readiness</Link>
        </Button>
        <Button variant={section === "filing-bundle" ? "default" : "outline"} asChild>
          <Link to={`/arbitration/cases/${caseId}/filing-bundle`}>Filing Bundle</Link>
        </Button>
      </div>

      {loading && (
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          Loading workspace
        </div>
      )}

      {!loading && section === "dashboard" && (
        <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
          <section className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2 text-base">
                  <Landmark className="h-4 w-4" />
                  Preparation Dashboard
                </CardTitle>
                <CardDescription>Matrix completion, drafts, and readiness blockers</CardDescription>
              </CardHeader>
              <CardContent className="grid gap-3 md:grid-cols-2">
                {MATRIX_DEFINITIONS.map((matrix) => (
                  <div key={matrix.slug} className="rounded-md border p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="font-medium">{matrix.label}</div>
                      <Badge variant="outline">{dashboard?.matrix_counts?.[matrix.slug] || 0}</Badge>
                    </div>
                    <div className="mt-1 text-xs text-muted-foreground">
                      Approved: {dashboard?.approved_counts?.[matrix.slug] || 0}
                    </div>
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Linked Drafts</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="mb-3 flex flex-wrap gap-2">
                  <Button size="sm" onClick={() => createDraftForCase("statement_of_claim")} disabled={saving}>
                    SoC
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => createDraftForCase("statement_of_defence")} disabled={saving}>
                    SoD
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => createDraftForCase("rejoinder")} disabled={saving}>
                    Rejoinder
                  </Button>
                  <Button size="sm" variant="outline" onClick={() => createDraftForCase("counterclaim")} disabled={saving}>
                    Counterclaim
                  </Button>
                </div>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Title</TableHead>
                      <TableHead>Type</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {(dashboard?.drafts || []).length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={4}>No drafts linked to this case.</TableCell>
                      </TableRow>
                    ) : (
                      (dashboard?.drafts || []).map((draft) => (
                        <TableRow key={String(draft._id)}>
                          <TableCell className="font-medium">{String(draft.title || "")}</TableCell>
                          <TableCell>{pretty(String(draft.draft_type || ""))}</TableCell>
                          <TableCell>
                            <Badge variant="outline">{String(draft.status || "")}</Badge>
                          </TableCell>
                          <TableCell className="text-right">
                            <Button size="sm" variant="outline" asChild>
                              <Link to={`/arbitration/drafts/${String(draft._id)}`}>Open</Link>
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </section>

          <aside className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Durable Pleading Workflow</CardTitle>
                <CardDescription>Server-selected engine, immutable inputs, human gates, and checkpointed progress</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="space-y-1">
                  <Label>Linked draft</Label>
                  <Select value={workflowDraftId} onValueChange={setWorkflowDraftId} disabled={Boolean(workflow)}>
                    <SelectTrigger><SelectValue placeholder="Select draft" /></SelectTrigger>
                    <SelectContent>
                      {(dashboard?.drafts || []).map((draft) => (
                        <SelectItem key={String(draft._id)} value={String(draft._id)}>
                          {String(draft.title)} ({pretty(String(draft.draft_type))})
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-1">
                  <Label>Selected document IDs</Label>
                  <Input
                    value={workflowDocumentIds}
                    onChange={(event) => setWorkflowDocumentIds(event.target.value)}
                    placeholder="Comma-separated scoped document IDs"
                  />
                </div>
                {selectedWorkflowDraft && ["statement_of_defence", "rejoinder"].includes(String(selectedWorkflowDraft.draft_type)) ? (
                  <div className="grid gap-2">
                    <div className="space-y-1">
                      <Label>{String(selectedWorkflowDraft.draft_type) === "rejoinder" ? "Statement of Claim draft" : "Opponent draft"}</Label>
                      <Select value={workflowOpponentDraftId} onValueChange={setWorkflowOpponentDraftId} disabled={Boolean(workflow)}>
                        <SelectTrigger><SelectValue placeholder="Select immutable pleading" /></SelectTrigger>
                        <SelectContent>
                          {(dashboard?.drafts || []).filter((draft) =>
                            String(draft._id) !== workflowDraftId
                            && (String(selectedWorkflowDraft.draft_type) !== "rejoinder" || String(draft.draft_type) === "statement_of_claim")
                          ).map((draft) => (
                            <SelectItem key={String(draft._id)} value={String(draft._id)}>{String(draft.title)}</SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    </div>
                    {String(selectedWorkflowDraft.draft_type) === "rejoinder" ? (
                      <>
                        <div className="space-y-1">
                          <Label>Statement of Defence draft</Label>
                          <Select value={workflowSecondOpponentDraftId} onValueChange={setWorkflowSecondOpponentDraftId} disabled={Boolean(workflow)}>
                            <SelectTrigger><SelectValue placeholder="Select SoD" /></SelectTrigger>
                            <SelectContent>
                              {(dashboard?.drafts || []).filter((draft) => String(draft.draft_type) === "statement_of_defence").map((draft) => (
                                <SelectItem key={String(draft._id)} value={String(draft._id)}>{String(draft.title)}</SelectItem>
                              ))}
                            </SelectContent>
                          </Select>
                        </div>
                        <div className="space-y-1">
                          <Label>SoD version ID</Label>
                          <Input value={workflowSecondOpponentVersionId} onChange={(event) => setWorkflowSecondOpponentVersionId(event.target.value)} />
                        </div>
                      </>
                    ) : null}
                    <div className="space-y-1">
                      <Label>Opponent version ID</Label>
                      <Input value={workflowOpponentVersionId} onChange={(event) => setWorkflowOpponentVersionId(event.target.value)} />
                    </div>
                  </div>
                ) : null}
                {!workflow ? (
                  <Button className="w-full" onClick={startWorkflow} disabled={workflowBusy || !workflowDraftId}>
                    {workflowBusy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <ShieldAlert className="mr-2 h-4 w-4" />}
                    Start governed workflow
                  </Button>
                ) : (
                  <div className="space-y-3 rounded-md border p-3">
                    <div className="flex items-center justify-between gap-2">
                      <Badge variant="outline">{pretty(workflow.status)}</Badge>
                      <span className="text-xs text-muted-foreground">v{workflow.state_version} · {workflow.engine}</span>
                    </div>
                    <Progress value={workflow.progress} />
                    <div className="text-sm font-medium">{pretty(workflow.current_node)}</div>
                    <div className="text-xs text-muted-foreground">
                      Next: {pretty(workflow.next_action)}
                      {workflow.required_human_role ? ` · Role: ${pretty(workflow.required_human_role)}` : ""}
                    </div>
                    {(workflow.blockers || []).length > 0 ? (
                      <div className="text-xs text-amber-700">{workflow.blockers.length} blocker(s) require review.</div>
                    ) : null}
                    {(workflow.validation_blockers || []).length > 0 ? (
                      <div className="space-y-1 rounded border border-red-200 bg-red-50 p-2 text-xs text-red-800">
                        <div className="font-medium">Draft validation blockers</div>
                        {workflow.validation_blockers.map((blocker, index) => (
                          <div key={`${String(blocker.code || "validation")}-${index}`}>
                            {String(blocker.message || blocker.code || "Validation blocker")}
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {workflow.validation_status ? (
                      <div className="text-xs text-muted-foreground">
                        Validation: {pretty(workflow.validation_status)}
                        {workflow.remediation_cycle ? ` - remediation cycle ${workflow.remediation_cycle}` : ""}
                      </div>
                    ) : null}
                    {workflow.current_node === "material_question_gate" ? (
                      <div className="space-y-2">
                        {(workflow.targeted_questions || []).map((question) => (
                          <div key={question.question_id} className="space-y-1">
                            <Label className="text-xs">{question.prompt}</Label>
                            <Textarea
                              value={workflowAnswers[question.question_id] || ""}
                              onChange={(event) => setWorkflowAnswers((previous) => ({ ...previous, [question.question_id]: event.target.value }))}
                              rows={2}
                            />
                          </div>
                        ))}
                      </div>
                    ) : null}
                    {workflow.plan_hash ? <div className="break-all text-xs text-muted-foreground">Plan: {workflow.plan_hash}</div> : null}
                    {workflow.draft_version_hash ? <div className="break-all text-xs text-muted-foreground">Version: {workflow.draft_version_hash}</div> : null}
                    {workflowEvents.length ? (
                      <div className="space-y-1 rounded border bg-muted/30 p-2" aria-label="Workflow timeline">
                        <div className="text-xs font-medium">Workflow timeline</div>
                        {workflowEvents.slice(-8).reverse().map((event, index) => (
                          <div key={String(event._id || `${event.event_type || "event"}-${index}`)} className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
                            <span>{pretty(String(event.event_type || "workflow_event"))}</span>
                            <span>{event.created_at ? new Date(String(event.created_at)).toLocaleString() : ""}</span>
                          </div>
                        ))}
                      </div>
                    ) : null}
                    <div className="flex gap-2">
                      {!['completed', 'cancelled', 'failed'].includes(workflow.status) ? (
                        <Button size="sm" onClick={continueWorkflow} disabled={workflowBusy}>
                          {workflow.current_node === "legal_review_gate" && workflow.validation_status !== "passed"
                            ? "Revalidate candidate"
                            : "Review / continue"}
                        </Button>
                      ) : null}
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={refreshWorkflow}
                      >
                        Refresh
                      </Button>
                      {!['completed', 'cancelled', 'failed'].includes(workflow.status) ? (
                        <Button
                          size="sm"
                          variant="destructive"
                          onClick={() => caseId && cancelArbitrationWorkflow(caseId, workflow.run_id, workflow.state_version, "Cancelled by user").then(setWorkflow)}
                        >
                          Cancel
                        </Button>
                      ) : null}
                    </div>
                  </div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Agent Runs</CardTitle>
                <CardDescription>Populate case matrices from scoped sources</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="space-y-2 rounded-md border p-2">
                  <div className="text-xs font-medium text-muted-foreground">Agent options (applied to every run)</div>
                  <div className="grid grid-cols-2 gap-2">
                    <div className="space-y-1">
                      <Label className="text-xs">Mode</Label>
                      <Select
                        value={agentOptions.agent_mode}
                        onValueChange={(value) => setAgentOptions((prev) => ({ ...prev, agent_mode: value }))}
                      >
                        <SelectTrigger className="h-8">
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectItem value="deterministic">Deterministic</SelectItem>
                          <SelectItem value="llm">LLM (needs review)</SelectItem>
                        </SelectContent>
                      </Select>
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Interest rate % p.a.</Label>
                      <Input
                        className="h-8"
                        type="number"
                        value={agentOptions.interest_rate}
                        onChange={(event) => setAgentOptions((prev) => ({ ...prev, interest_rate: event.target.value }))}
                        aria-label="Interest rate percent per annum"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Interest period (days)</Label>
                      <Input
                        className="h-8"
                        type="number"
                        value={agentOptions.interest_period_days}
                        onChange={(event) => setAgentOptions((prev) => ({ ...prev, interest_period_days: event.target.value }))}
                        aria-label="Interest period in days"
                      />
                    </div>
                    <div className="space-y-1">
                      <Label className="text-xs">Limitation period (years)</Label>
                      <Input
                        className="h-8"
                        type="number"
                        value={agentOptions.limitation_period_years}
                        onChange={(event) => setAgentOptions((prev) => ({ ...prev, limitation_period_years: event.target.value }))}
                        aria-label="Limitation period in years"
                      />
                    </div>
                    <div className="col-span-2 space-y-1">
                      <Label className="text-xs">Cause of action date (YYYY-MM-DD)</Label>
                      <Input
                        className="h-8"
                        value={agentOptions.cause_of_action_date}
                        onChange={(event) => setAgentOptions((prev) => ({ ...prev, cause_of_action_date: event.target.value }))}
                        aria-label="Cause of action date"
                      />
                    </div>
                  </div>
                </div>
                <div className="grid grid-cols-2 gap-2">
                  {AGENT_ACTIONS.map((action) => (
                    <Button
                      key={action.type}
                      size="sm"
                      variant={action.type === "orchestrator" ? "default" : "outline"}
                      className="justify-start"
                      onClick={() => runAgent(action.type)}
                      disabled={Boolean(runningAgent)}
                    >
                      {runningAgent === action.type ? (
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      ) : (
                        <Sparkles className="mr-2 h-4 w-4" />
                      )}
                      {action.label}
                    </Button>
                  ))}
                </div>
                <Button
                  className="w-full"
                  variant="secondary"
                  onClick={() => queueAgent("orchestrator")}
                  disabled={Boolean(runningAgent || queueingAgent)}
                >
                  {queueingAgent === "orchestrator" ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    <RefreshCw className="mr-2 h-4 w-4" />
                  )}
                  Queue Orchestrator Job
                </Button>
                {(dashboard?.latest_agent_runs || []).slice(0, 5).map((run) => (
                  <div key={run._id} className="rounded-md border p-2 text-sm">
                    <div className="flex items-start justify-between gap-2">
                      <div className="font-medium">{pretty(run.agent_type)}</div>
                      <Badge variant="outline">{run.created_records?.length || 0} rows</Badge>
                    </div>
                    <div className="text-xs text-muted-foreground">{run.status}</div>
                    {run.output_summary ? (
                      <div className="mt-1 text-xs text-muted-foreground">{run.output_summary}</div>
                    ) : null}
                    {(run.warnings?.length || 0) > 0 ? (
                      <div className="mt-1 text-xs text-amber-700">{run.warnings?.length} warning(s)</div>
                    ) : null}
                  </div>
                ))}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Current Blockers</CardTitle>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                {(currentReadiness?.blockers || []).length === 0 ? (
                  <div className="flex items-center gap-2 text-emerald-700">
                    <CheckCircle2 className="h-4 w-4" />
                    No readiness blockers
                  </div>
                ) : (
                  (currentReadiness?.blockers || []).map((blocker) => (
                    <div key={blocker._id} className="rounded-md border border-amber-200 bg-amber-50 p-2 text-amber-900">
                      {blocker.message}
                    </div>
                  ))
                )}
              </CardContent>
            </Card>
          </aside>
        </div>
      )}

      {!loading && section === "matrices" && (
        <div className="space-y-4">
          <Tabs value={activeMatrix} onValueChange={(value) => setActiveMatrix(value as MatrixSlug)}>
            <TabsList className="flex h-auto flex-wrap justify-start">
              {MATRIX_DEFINITIONS.map((matrix) => (
                <TabsTrigger key={matrix.slug} value={matrix.slug}>
                  {matrix.label}
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>

          <div className="grid gap-4 xl:grid-cols-[380px_1fr]">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Add Row</CardTitle>
                <CardDescription>{MATRIX_DEFINITIONS.find((item) => item.slug === activeMatrix)?.label}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-3">
                {(MATRIX_FIELDS[activeMatrix] || []).map((field) => (
                  <div key={field.key} className="space-y-2">
                    <Label>{field.label}</Label>
                    {field.type === "textarea" ? (
                      <Textarea value={matrixForm[field.key] || ""} onChange={(event) => setMatrixForm((prev) => ({ ...prev, [field.key]: event.target.value }))} rows={3} />
                    ) : field.type === "select" ? (
                      <Select
                        value={matrixForm[field.key] || (field.options?.[0] ?? "")}
                        onValueChange={(value) => setMatrixForm((prev) => ({ ...prev, [field.key]: value }))}
                      >
                        <SelectTrigger>
                          <SelectValue />
                        </SelectTrigger>
                        <SelectContent>
                          {(field.options || []).map((option) => (
                            <SelectItem key={option} value={option}>
                              {option}
                            </SelectItem>
                          ))}
                        </SelectContent>
                      </Select>
                    ) : (
                      <Input type={field.type || "text"} value={matrixForm[field.key] || ""} onChange={(event) => setMatrixForm((prev) => ({ ...prev, [field.key]: event.target.value }))} />
                    )}
                  </div>
                ))}
                <p className="text-xs text-muted-foreground">
                  New rows always begin in needs review. Approval is available only through the role-based review action.
                </p>
                <Button className="w-full" onClick={saveMatrixRow} disabled={saving}>
                  {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                  Save Row
                </Button>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Matrix Rows</CardTitle>
                <CardDescription>Rows persist as case-level preparation records</CardDescription>
              </CardHeader>
              <CardContent>
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Content</TableHead>
                      <TableHead>Status</TableHead>
                      <TableHead className="text-right">Action</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {rows.length === 0 ? (
                      <TableRow>
                        <TableCell colSpan={3}>No rows in this matrix.</TableCell>
                      </TableRow>
                    ) : (
                      rows.map((row) => (
                        <TableRow key={row._id}>
                          <TableCell>
                            <div className="space-y-1">
                              {usefulRowEntries(row).map(([key, value]) => (
                                <div key={key} className="text-sm">
                                  <span className="font-medium">{pretty(key)}:</span> {String(value)}
                                </div>
                              ))}
                            </div>
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-wrap gap-1">
                              {row.approval_status && <Badge variant="outline">{String(row.approval_status)}</Badge>}
                              {row.verification_status && <Badge variant="outline">{String(row.verification_status)}</Badge>}
                              {row.readiness_status && <Badge variant="outline">{String(row.readiness_status)}</Badge>}
                              {row.review_status ? <Badge variant="outline">{pretty(String(row.review_status))}</Badge> : null}
                            </div>
                            {stringArray(row.review_required_roles).length > 0 ? (
                              <div className="mt-2 text-xs text-muted-foreground">
                                Review: {stringArray(row.review_completed_roles).join(", ") || "none"} / {stringArray(row.review_required_roles).join(", ")}
                              </div>
                            ) : null}
                            {Array.isArray(row.review_comments) && row.review_comments.length > 0 ? (
                              <div className="mt-1 text-xs text-muted-foreground">{row.review_comments.length} comment(s)</div>
                            ) : null}
                          </TableCell>
                          <TableCell>
                            <div className="flex flex-col items-end gap-2">
                              <Select
                                value={reviewRoles[row._id] || defaultReviewRole(activeMatrix)}
                                onValueChange={(value) => setReviewRoles((prev) => ({ ...prev, [row._id]: value }))}
                              >
                                <SelectTrigger className="h-8 w-36">
                                  <SelectValue />
                                </SelectTrigger>
                                <SelectContent>
                                  {REVIEW_ROLES.map((role) => (
                                    <SelectItem key={role} value={role}>
                                      {pretty(role)}
                                    </SelectItem>
                                  ))}
                                </SelectContent>
                              </Select>
                              <div className="flex flex-wrap justify-end gap-2">
                                <Button size="sm" variant="outline" onClick={() => submitReviewAction(row, "assign")}>
                                  Assign
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => submitReviewAction(row, "comment")}>
                                  Comment
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => submitReviewAction(row, "request_changes")}>
                                  Needs Work
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => submitReviewAction(row, "approve")}>
                                  Approve
                                </Button>
                                <Button size="sm" variant="outline" onClick={() => submitReviewAction(row, "reject")}>
                                  Reject
                                </Button>
                              </div>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))
                    )}
                  </TableBody>
                </Table>
              </CardContent>
            </Card>
          </div>
        </div>
      )}

      {!loading && section === "readiness" && (
        <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <ShieldAlert className="h-4 w-4" />
                Readiness Checks
              </CardTitle>
              <CardDescription>Draft generation is blocked until linked-case checks are ready</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {(currentReadiness?.checks || []).map((check) => (
                <div key={check._id} className="flex flex-wrap items-start justify-between gap-3 rounded-md border p-3">
                  <div>
                    <div className="font-medium">{pretty(check.check_key)}</div>
                    <div className="text-sm text-muted-foreground">{check.message}</div>
                  </div>
                  <Badge variant={check.status === "ready" ? "default" : "outline"}>{pretty(check.status)}</Badge>
                </div>
              ))}
            </CardContent>
          </Card>
          <aside className="space-y-3 rounded-md border bg-background p-4">
            <h2 className="text-base font-medium">Gate Control</h2>
            <div className="text-sm text-muted-foreground">Score: {currentReadiness?.readiness_score ?? 0}%</div>
            <Progress value={currentReadiness?.readiness_score ?? 0} />
            <Button className="w-full" onClick={approveReadiness} disabled={saving || (currentReadiness?.blockers || []).length > 0}>
              <CheckCircle2 className="mr-2 h-4 w-4" />
              Approve Readiness
            </Button>
          </aside>
        </div>
      )}

      {!loading && section === "filing-bundle" && (
        <div className="grid gap-4 xl:grid-cols-[1fr_340px]">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2 text-base">
                <Archive className="h-4 w-4" />
                Filing Bundle
              </CardTitle>
              <CardDescription>Exhibit list, citation audit, and case bundle export</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="rounded-md border p-3">
                <div className="font-medium">Citation audit</div>
                <div className="mt-1 text-sm text-muted-foreground">
                  {manifest?.citation_audit?.ok ? "All cited exhibits resolve to the exhibit list." : "Missing exhibit references need correction before filing."}
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs">
                  <Badge variant="outline">Blocking {manifest?.citation_audit?.blocking_issue_count || 0}</Badge>
                  <Badge variant="outline">Warnings {manifest?.citation_audit?.warning_issue_count || 0}</Badge>
                </div>
                {(manifest?.citation_audit?.missing_exhibits || []).length > 0 && (
                  <div className="mt-2 text-sm text-red-700">
                    Missing: {(manifest?.citation_audit?.missing_exhibits || []).join(", ")}
                  </div>
                )}
              </div>
              {(manifest?.citation_audit?.issues || []).length > 0 && (
                <div className="space-y-2">
                  {(manifest?.citation_audit?.issues || []).slice(0, 8).map((issue, index) => (
                    <div key={`${issue.issue_type || "issue"}-${index}`} className="rounded-md border p-2 text-sm">
                      <div className="font-medium">{pretty(issue.issue_type || "audit issue")}</div>
                      <div className="text-muted-foreground">{issue.message}</div>
                    </div>
                  ))}
                </div>
              )}
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Exhibit</TableHead>
                    <TableHead>Title</TableHead>
                    <TableHead>Source</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {(manifest?.exhibit_list || []).length === 0 ? (
                    <TableRow>
                      <TableCell colSpan={3}>No exhibits assigned.</TableCell>
                    </TableRow>
                  ) : (
                    (manifest?.exhibit_list || []).map((item) => (
                      <TableRow key={item._id}>
                        <TableCell className="font-medium">{String(item.exhibit_id || "")}</TableCell>
                        <TableCell>{String(item.title || item.document_type || "")}</TableCell>
                        <TableCell>{String(item.source_id || "")}</TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
          <aside className="space-y-3 rounded-md border bg-background p-4">
            <h2 className="text-base font-medium">Export</h2>
            <p className="text-xs text-muted-foreground">
              Direct download streams the full bundle including exhibit files — use it for large cases.
            </p>
            <Button className="w-full" onClick={() => downloadBundle("zip")}>
              <Download className="mr-2 h-4 w-4" />
              Download ZIP Bundle
            </Button>
            <Button className="w-full" variant="outline" onClick={() => downloadBundle("docx")}>
              <Download className="mr-2 h-4 w-4" />
              Download DOCX Summary
            </Button>
            <Button className="w-full" variant="outline" onClick={() => downloadBundle("pdf")}>
              <Download className="mr-2 h-4 w-4" />
              Download PDF Summary
            </Button>
            <div className="space-y-1 border-t pt-3">
              <div className="text-xs font-medium text-muted-foreground">Queued export (background job)</div>
              <p className="text-xs text-muted-foreground">
                Inline queued exports are capped at 12 MB. If a case has many exhibit files, use Direct download above instead.
              </p>
              <div className="grid grid-cols-3 gap-2">
                {(["zip", "docx", "pdf"] as BundleExportFormat[]).map((format) => (
                  <Button
                    key={format}
                    size="sm"
                    variant="secondary"
                    onClick={() => queueBundleExport(format)}
                    disabled={Boolean(queueingExport)}
                  >
                    {queueingExport === format ? <Loader2 className="h-4 w-4 animate-spin" /> : format.toUpperCase()}
                  </Button>
                ))}
              </div>
            </div>
            {queuedExport ? (
              <div className="rounded-md border p-3 text-sm">
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium">{queuedExport.format.toUpperCase()} export</span>
                  <Badge variant="outline">{queuedExport.status}</Badge>
                </div>
                <div className="mt-1 text-xs text-muted-foreground">
                  {queuedExport.content_length ? `${queuedExport.content_length} bytes` : queuedExport.background_job_id || "Queued"}
                  {queuedExport.attempts ? ` · attempt ${queuedExport.attempts}` : ""}
                </div>
                <div className="mt-3 grid grid-cols-2 gap-2">
                  <Button size="sm" variant="outline" onClick={refreshQueuedExport}>
                    <RefreshCw className="mr-2 h-4 w-4" />
                    Refresh
                  </Button>
                  <Button size="sm" onClick={downloadQueuedExport} disabled={queuedExport.status !== "completed"}>
                    <Download className="mr-2 h-4 w-4" />
                    Result
                  </Button>
                </div>
              </div>
            ) : null}
            <p className="text-sm text-muted-foreground">
              ZIP includes manifest, exhibit list, citation audit, readiness, matrix JSON exports, and latest draft artifacts.
            </p>
          </aside>
        </div>
      )}
    </div>
  );
};

export default ArbitrationCaseWorkspacePage;
