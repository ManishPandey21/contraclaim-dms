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
import { Textarea } from "@/components/ui/textarea";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FilePlus2,
  Loader2,
  Lock,
  Pencil,
  Plus,
  RefreshCw,
  Search,
  Sparkles,
  Trash2,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";
import { enhancedApi } from "@/services/enhanced-api";
import {
  ArbitrationDraft,
  ArbitrationDraftCreatePayload,
  ArbitrationDraftType,
  ArbitrationDraftVersion,
  ArbitrationReferenceInput,
  addArbitrationDraftReferences,
  approveArbitrationDraft,
  createArbitrationDraft,
  exportArbitrationDraft,
  generateArbitrationDraft,
  getArbitrationDraft,
  getArbitrationDraftVersion,
  importDefenceParagraphs,
  importSocParagraphs,
  listArbitrationDraftVersions,
  listArbitrationDrafts,
  regenerateArbitrationSection,
  removeArbitrationDraftReference,
  returnArbitrationDraftForRevision,
  saveArbitrationDraftVersion,
  searchArbitrationEvidence,
  updateArbitrationDraft,
} from "@/services/arbitration-drafting-api";
import { ArbitrationCase, listArbitrationCases } from "@/services/arbitration-cases-api";

const REGISTER_ORIGINS = new Set([
  "claim_register",
  "variation_register",
  "ipc_register",
  "bank_guarantee_register",
]);

const apiErrorMessage = (error: unknown, fallback: string): string => {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const info = detail as { message?: string; blockers?: Array<{ message?: string } | string> };
    const blockers = (info.blockers || [])
      .map((item) => (typeof item === "string" ? item : item?.message || ""))
      .filter(Boolean)
      .slice(0, 3);
    return [info.message, ...blockers].filter(Boolean).join(" — ") || fallback;
  }
  return fallback;
};

const DRAFT_TYPES: Record<string, { value: ArbitrationDraftType; label: string; role: "claimant" | "respondent" }> = {
  claim: { value: "statement_of_claim", label: "Statement of Claim", role: "claimant" },
  defence: { value: "statement_of_defence", label: "Statement of Defence", role: "respondent" },
  rejoinder: { value: "rejoinder", label: "Rejoinder / Reply to Defence", role: "claimant" },
  counterclaim: { value: "counterclaim", label: "Counterclaim", role: "respondent" },
};

const DISPUTE_TYPES = [
  ["eot_delay", "EOT / Delay"],
  ["prolongation_cost", "Prolongation Cost"],
  ["price_variation", "Price Variation"],
  ["variation_change_order", "Variation / Change Order"],
  ["payment_dispute", "Payment Dispute"],
  ["termination", "Termination"],
  ["force_majeure", "Force Majeure"],
  ["defect_dlp", "Defect / DLP"],
  ["bank_guarantee_retention", "Bank Guarantee / Retention"],
  ["counterclaim", "Counterclaim"],
  ["other", "Other"],
];

type ProjectOption = { id?: string; _id?: string; name?: string; organization_id?: string };

interface FormState {
  organization_id: string;
  project_id: string;
  case_id: string;
  include_registers: string;
  title: string;
  dispute_type: string;
  party_role: "claimant" | "respondent";
  tribunal_details: string;
  arbitration_clause: string;
  governing_law: string;
  relief_sought: string;
  manual_facts: string;
  claim_amount: string;
  currency: string;
  interest_rate: string;
  reference_label: string;
  reference_snippet: string;
  claim_head: string;
}

const initialForm = (kind: string): FormState => ({
  organization_id: "",
  project_id: "",
  case_id: "none",
  include_registers: "yes",
  title: DRAFT_TYPES[kind]?.label || "Arbitration Draft",
  dispute_type: "eot_delay",
  party_role: DRAFT_TYPES[kind]?.role || "claimant",
  tribunal_details: "",
  arbitration_clause: "",
  governing_law: "",
  relief_sought: "",
  manual_facts: "",
  claim_amount: "",
  currency: "INR",
  interest_rate: "",
  reference_label: "",
  reference_snippet: "",
  claim_head: "",
});

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

const ArbitrationDraftingPage: React.FC = () => {
  const { draftId } = useParams();
  const location = useLocation();
  const navigate = useNavigate();
  const kind = useMemo(() => {
    const segment = location.pathname.split("/").filter(Boolean).pop() || "drafts";
    return DRAFT_TYPES[segment] ? segment : "drafts";
  }, [location.pathname]);
  const draftType = DRAFT_TYPES[kind];

  const [drafts, setDrafts] = useState<ArbitrationDraft[]>([]);
  const [draft, setDraft] = useState<ArbitrationDraft | null>(null);
  const [projects, setProjects] = useState<ProjectOption[]>([]);
  const [cases, setCases] = useState<ArbitrationCase[]>([]);
  const [form, setForm] = useState<FormState>(() => initialForm(kind));
  const [pleadingText, setPleadingText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);
  const [selectedSection, setSelectedSection] = useState("");
  const [versions, setVersions] = useState<ArbitrationDraftVersion[]>([]);
  const [viewedVersion, setViewedVersion] = useState<ArbitrationDraftVersion | null>(null);
  const [editing, setEditing] = useState(false);
  const [editText, setEditText] = useState("");
  const [approving, setApproving] = useState(false);
  const [draftMode, setDraftMode] = useState<"deterministic" | "llm">("deterministic");
  const [evidenceQuery, setEvidenceQuery] = useState("");
  const [evidenceResults, setEvidenceResults] = useState<ArbitrationReferenceInput[]>([]);
  const [searchingEvidence, setSearchingEvidence] = useState(false);

  const loadDrafts = useCallback(async () => {
    setLoading(true);
    try {
      setDrafts(await listArbitrationDrafts());
    } catch {
      toast.error("Failed to load arbitration drafts");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadDraft = useCallback(async () => {
    if (!draftId) return;
    setLoading(true);
    try {
      setDraft(await getArbitrationDraft(draftId));
      setViewedVersion(null);
      setEditing(false);
      try {
        setVersions(await listArbitrationDraftVersions(draftId));
      } catch {
        setVersions([]);
      }
    } catch {
      toast.error("Failed to load arbitration draft");
    } finally {
      setLoading(false);
    }
  }, [draftId]);

  useEffect(() => {
    enhancedApi.getProjects().then(setProjects).catch(() => setProjects([]));
    listArbitrationCases().then(setCases).catch(() => setCases([]));
  }, []);

  useEffect(() => {
    setForm(initialForm(kind));
  }, [kind]);

  useEffect(() => {
    if (draftId) loadDraft();
    else loadDrafts();
  }, [draftId, loadDraft, loadDrafts]);

  const update = (key: keyof FormState, value: string) => {
    setForm((prev) => {
      const next = { ...prev, [key]: value };
      if (key === "project_id") {
        const project = projects.find((item) => projectId(item) === value);
        next.organization_id = project?.organization_id || prev.organization_id;
      }
      return next;
    });
  };

  const createDraft = async (generateAfter = false) => {
    if (!draftType || !form.project_id || !form.title.trim()) {
      toast.error("Project and title are required");
      return;
    }
    setSaving(true);
    try {
      const payload: ArbitrationDraftCreatePayload = {
        organization_id: form.organization_id || undefined,
        project_id: form.project_id,
        case_id: form.case_id && form.case_id !== "none" ? form.case_id : undefined,
        include_register_sources: form.include_registers !== "no",
        draft_type: draftType.value,
        party_role: form.party_role,
        dispute_type: form.dispute_type,
        title: form.title,
        tribunal_details: form.tribunal_details || undefined,
        arbitration_clause: form.arbitration_clause || undefined,
        governing_law: form.governing_law || undefined,
        relief_sought: form.relief_sought || undefined,
        manual_facts: form.manual_facts || undefined,
        claim_amount: form.claim_amount ? Number(form.claim_amount) : undefined,
        currency: form.currency || undefined,
        interest_rate: form.interest_rate ? Number(form.interest_rate) : undefined,
        selected_references: form.reference_label
          ? [
              {
                source_type: "manual_fact",
                source_id: `manual:${Date.now()}`,
                label: form.reference_label,
                snippet: form.reference_snippet,
                allowed_use: "fact",
              },
            ]
          : [],
        claim_heads: form.claim_head
          ? [
              {
                head_type: "other",
                description: form.claim_head,
                amount: form.claim_amount ? Number(form.claim_amount) : undefined,
                currency: form.currency || undefined,
                status: "evidence_required",
              },
            ]
          : [],
      };
      const created = await createArbitrationDraft(payload);
      if (generateAfter) {
        const generated = await generateArbitrationDraft(created._id, {});
        navigate(`/arbitration/drafts/${generated._id}`);
      } else {
        navigate(`/arbitration/drafts/${created._id}`);
      }
      toast.success("Arbitration draft created");
    } catch {
      toast.error("Unable to create arbitration draft");
    } finally {
      setSaving(false);
    }
  };

  const generate = async () => {
    if (!draft) return;
    setGenerating(true);
    try {
      const next = await generateArbitrationDraft(draft._id, { draft_mode: draftMode });
      setDraft(next);
      toast.success(draftMode === "llm" ? "Draft generated (AI prose)" : "Draft generated");
    } catch {
      toast.error("Draft generation failed");
    } finally {
      setGenerating(false);
    }
  };

  const regenerateSection = async () => {
    if (!draft || !selectedSection) return;
    setGenerating(true);
    try {
      const next = await regenerateArbitrationSection(draft._id, selectedSection);
      setDraft(next);
      toast.success("Section regenerated");
    } catch {
      toast.error("Section regeneration failed");
    } finally {
      setGenerating(false);
    }
  };

  const importParagraphs = async () => {
    if (!draft || !pleadingText.trim()) return;
    setGenerating(true);
    try {
      if (draft.draft_type === "rejoinder") {
        await importDefenceParagraphs(draft._id, pleadingText);
      } else {
        await importSocParagraphs(draft._id, pleadingText);
      }
      setDraft(await getArbitrationDraft(draft._id));
      setPleadingText("");
      toast.success("Paragraphs imported");
    } catch {
      toast.error("Unable to import pleading paragraphs");
    } finally {
      setGenerating(false);
    }
  };

  const exportDraft = async (format: "docx" | "pdf") => {
    if (!draft) return;
    try {
      const blob = await exportArbitrationDraft(draft._id, format);
      downloadBlob(blob, `${draft.title}.${format}`);
    } catch {
      toast.error(`Unable to export ${format.toUpperCase()}`);
    }
  };

  const approveDraft = async () => {
    if (!draft) return;
    setApproving(true);
    try {
      setDraft(await approveArbitrationDraft(draft._id));
      toast.success("Draft approved and locked");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Approval blocked"));
    } finally {
      setApproving(false);
    }
  };

  const returnDraft = async () => {
    if (!draft) return;
    const reason = window.prompt("Reason for returning this draft for revision")?.trim();
    if (!reason) return;
    setApproving(true);
    try {
      setDraft(await returnArbitrationDraftForRevision(draft._id, reason));
      toast.success("Draft returned for revision");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Unable to return draft for revision"));
    } finally {
      setApproving(false);
    }
  };

  const openVersion = async (versionNo: number) => {
    if (!draft) return;
    if (versionNo === (draft.current_version || 0)) {
      setViewedVersion(null);
      return;
    }
    try {
      setViewedVersion(await getArbitrationDraftVersion(draft._id, versionNo));
    } catch {
      toast.error("Unable to load draft version");
    }
  };

  const saveManualVersion = async () => {
    if (!draft || !editText.trim()) return;
    setGenerating(true);
    try {
      await saveArbitrationDraftVersion(draft._id, editText);
      setEditing(false);
      await loadDraft();
      toast.success("Edited version saved");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Unable to save edited version"));
    } finally {
      setGenerating(false);
    }
  };

  const runEvidenceSearch = async () => {
    if (!draft || !evidenceQuery.trim()) return;
    setSearchingEvidence(true);
    try {
      setEvidenceResults(await searchArbitrationEvidence(draft._id, evidenceQuery));
    } catch {
      toast.error("Evidence search failed");
    } finally {
      setSearchingEvidence(false);
    }
  };

  const addReference = async (reference: ArbitrationReferenceInput) => {
    if (!draft) return;
    try {
      setDraft(await addArbitrationDraftReferences(draft._id, [reference]));
      setEvidenceResults((prev) => prev.filter((item) => item.source_id !== reference.source_id));
      toast.success("Evidence added to the draft");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Unable to add evidence"));
    }
  };

  const removeReference = async (referenceId?: string) => {
    if (!draft || !referenceId) return;
    try {
      setDraft(await removeArbitrationDraftReference(draft._id, referenceId));
      toast.success("Evidence removed");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Unable to remove evidence"));
    }
  };

  const excludeRegisterSource = async (sourceId: string) => {
    if (!draft) return;
    try {
      const excluded = Array.from(new Set([...(draft.excluded_register_ids || []), sourceId]));
      setDraft(await updateArbitrationDraft(draft._id, { excluded_register_ids: excluded }));
      toast.success("Register source excluded — regenerate to refresh the ledger");
    } catch (error) {
      toast.error(apiErrorMessage(error, "Unable to exclude register source"));
    }
  };

  if (draftId) {
    const latestVersion = draft?.latest_version;
    const markdown = latestVersion?.full_markdown || "";
    const sections = latestVersion?.sections || [];
    const safetyWarnings = latestVersion?.warnings || latestVersion?.structured_output?.validation_warnings || [];
    const approvalBlockers = latestVersion?.structured_output?.approval_blockers || [];
    const sourceLedger = latestVersion?.source_ledger || [];
    const isLocked = Boolean(draft?.is_locked);
    const displayedVersion = viewedVersion || latestVersion;
    const displayedMarkdown = displayedVersion?.full_markdown || "";
    const selectedReferences = draft?.selected_references || [];
    return (
      <div className="space-y-6 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-2xl font-semibold tracking-normal">Arbitration Draft</h1>
            <p className="text-sm text-muted-foreground">{draft?.title || "Loading draft"}</p>
          </div>
          <div className="flex flex-wrap gap-2">
            <Button variant="outline" asChild>
              <Link to="/arbitration/drafts">Saved Drafts</Link>
            </Button>
            {draft?.case_id && (
              <Button variant="outline" asChild>
                <Link to={`/arbitration/cases/${draft.case_id}`}>Case Workspace</Link>
              </Button>
            )}
            <Select value={draftMode} onValueChange={(value) => setDraftMode(value as "deterministic" | "llm")}>
              <SelectTrigger className="w-[150px]" aria-label="Draft mode">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="deterministic">Source-grounded</SelectItem>
                <SelectItem value="llm">AI prose</SelectItem>
              </SelectContent>
            </Select>
            <Button onClick={generate} disabled={generating || !draft || isLocked}>
              {generating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
              Generate
            </Button>
            {isLocked ? (
              <Button variant="outline" onClick={returnDraft} disabled={approving || !draft}>
                <Undo2 className="mr-2 h-4 w-4" />
                Return for Revision
              </Button>
            ) : (
              <Button variant="outline" onClick={approveDraft} disabled={approving || !draft || !markdown}>
                {approving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <CheckCircle2 className="mr-2 h-4 w-4" />}
                Approve
              </Button>
            )}
            <Button variant="outline" onClick={() => exportDraft("docx")} disabled={!markdown}>
              <Download className="mr-2 h-4 w-4" />
              DOCX
            </Button>
            <Button variant="outline" onClick={() => exportDraft("pdf")} disabled={!markdown}>
              <Download className="mr-2 h-4 w-4" />
              PDF
            </Button>
          </div>
        </div>

        {!loading && draft && !draft.case_id && (
          <div className="flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-3 text-sm text-amber-900">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
            <div>
              <span className="font-medium">Ungated draft.</span> This draft is not linked to a case workspace, so the
              matrix readiness gate (jurisdiction, limitation, quantum, expert alignment) was not applied.{" "}
              <Link to="/arbitration/cases" className="underline">
                Link it to a case workspace
              </Link>{" "}
              for a gated filing.
            </div>
          </div>
        )}

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading arbitration draft
          </div>
        ) : (
          <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
            <section className="rounded-md border bg-background p-4">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Badge variant="outline">{pretty(draft?.draft_type)}</Badge>
                <Badge variant="neutral">{draft?.status}</Badge>
                <Badge variant="outline">Version {draft?.current_version || 0}</Badge>
                {isLocked && (
                  <Badge variant="outline" className="border-emerald-300 text-emerald-700">
                    <Lock className="mr-1 h-3 w-3" />
                    Locked (approved)
                  </Badge>
                )}
                {viewedVersion && (
                  <Badge variant="outline" className="border-blue-300 text-blue-700">
                    Viewing v{viewedVersion.version}
                  </Badge>
                )}
                <div className="ml-auto flex gap-2">
                  {editing ? (
                    <>
                      <Button size="sm" onClick={saveManualVersion} disabled={generating || !editText.trim()}>
                        Save as New Version
                      </Button>
                      <Button size="sm" variant="outline" onClick={() => setEditing(false)} disabled={generating}>
                        Cancel
                      </Button>
                    </>
                  ) : (
                    <Button
                      size="sm"
                      variant="outline"
                      onClick={() => {
                        setEditText(displayedMarkdown);
                        setEditing(true);
                      }}
                      disabled={isLocked || !displayedMarkdown}
                    >
                      <Pencil className="mr-2 h-3.5 w-3.5" />
                      Edit
                    </Button>
                  )}
                </div>
              </div>
              {editing ? (
                <Textarea
                  value={editText}
                  onChange={(event) => setEditText(event.target.value)}
                  className="min-h-[520px] font-mono text-sm leading-6"
                  aria-label="Draft markdown editor"
                />
              ) : (
                <pre className="min-h-[520px] whitespace-pre-wrap rounded-md bg-muted/40 p-4 text-sm leading-6">
                  {displayedMarkdown || "Generate the first version to see the pleading draft here."}
                </pre>
              )}
            </section>

            <aside className="space-y-4">
              {(draft?.draft_type === "statement_of_defence" || draft?.draft_type === "rejoinder") && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Import Pleading</CardTitle>
                    <CardDescription>
                      {draft?.draft_type === "rejoinder" ? "Paste the SoD for paragraph-wise replies." : "Paste the SoC for paragraph-wise responses."}
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <Textarea value={pleadingText} onChange={(e) => setPleadingText(e.target.value)} rows={7} />
                    <Button className="w-full" onClick={importParagraphs} disabled={generating || isLocked || !pleadingText.trim()}>
                      <RefreshCw className="mr-2 h-4 w-4" />
                      Import Paragraphs
                    </Button>
                  </CardContent>
                </Card>
              )}

              {sections.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Section Regeneration</CardTitle>
                    <CardDescription>Regenerate one section using the same source-ledger guardrails</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <Select value={selectedSection} onValueChange={setSelectedSection}>
                      <SelectTrigger>
                        <SelectValue placeholder="Select section" />
                      </SelectTrigger>
                      <SelectContent>
                        {sections.map((section) => (
                          <SelectItem key={section.key} value={section.key}>
                            {section.heading}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button className="w-full" variant="outline" onClick={regenerateSection} disabled={generating || isLocked || !selectedSection}>
                      {generating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
                      Regenerate Section
                    </Button>
                  </CardContent>
                </Card>
              )}

              {versions.length > 0 && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Version History</CardTitle>
                    <CardDescription>Every generation and manual edit is an immutable version</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-2 text-sm">
                    {versions.map((item) => {
                      const isCurrent = item.version === (draft?.current_version || 0) && !viewedVersion;
                      const isViewed = viewedVersion?.version === item.version;
                      return (
                        <button
                          key={item._id || item.version}
                          type="button"
                          onClick={() => openVersion(item.version)}
                          className={`flex w-full items-center justify-between rounded-md border p-2 text-left hover:bg-muted/50 ${
                            isCurrent || isViewed ? "border-primary" : ""
                          }`}
                        >
                          <span className="font-medium">v{item.version}</span>
                          <span className="text-xs text-muted-foreground">
                            {pretty(item.validation_status) || "not checked"}
                            {item.created_at ? ` · ${new Date(item.created_at).toLocaleString()}` : ""}
                          </span>
                        </button>
                      );
                    })}
                    {viewedVersion && (
                      <Button size="sm" variant="outline" className="w-full" onClick={() => setViewedVersion(null)}>
                        Back to Latest
                      </Button>
                    )}
                  </CardContent>
                </Card>
              )}

              {!isLocked && (
                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">Evidence Search</CardTitle>
                    <CardDescription>Search project documents and clauses, then link them to this draft</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3 text-sm">
                    <div className="flex gap-2">
                      <Input
                        value={evidenceQuery}
                        onChange={(event) => setEvidenceQuery(event.target.value)}
                        placeholder="e.g. site access, EOT notice, clause 2.1"
                        onKeyDown={(event) => {
                          if (event.key === "Enter") runEvidenceSearch();
                        }}
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={runEvidenceSearch}
                        disabled={searchingEvidence || !evidenceQuery.trim()}
                        aria-label="Search evidence"
                      >
                        {searchingEvidence ? <Loader2 className="h-4 w-4 animate-spin" /> : <Search className="h-4 w-4" />}
                      </Button>
                    </div>
                    {evidenceResults.map((result) => (
                      <div key={`${result.source_type}-${result.source_id}`} className="rounded-md border p-2">
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <div className="font-medium">{result.label}</div>
                            {result.citation && <div className="text-xs text-muted-foreground">{result.citation}</div>}
                            {result.snippet && <div className="mt-1 text-xs text-muted-foreground">{result.snippet.slice(0, 160)}</div>}
                          </div>
                          <Button size="sm" variant="outline" onClick={() => addReference(result)} aria-label={`Add ${result.label}`}>
                            <Plus className="h-3.5 w-3.5" />
                          </Button>
                        </div>
                      </div>
                    ))}
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Evidence Status</CardTitle>
                  <CardDescription>Source ledger and missing proof markers</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <div>Sources: {sourceLedger.length || draft?.selected_references?.length || 0}</div>
                  {selectedReferences.length > 0 && (
                    <div className="space-y-2">
                      <div className="text-xs font-medium text-muted-foreground">Selected references</div>
                      {selectedReferences.slice(0, 8).map((reference, idx) => (
                        <div key={reference._id || `${reference.source_id}-${idx}`} className="flex items-start justify-between gap-2 rounded-md border p-2">
                          <div>
                            <div className="font-medium">{reference.label}</div>
                            {reference.citation && <div className="text-xs text-muted-foreground">{reference.citation}</div>}
                          </div>
                          {!isLocked && reference._id && (
                            <Button size="sm" variant="ghost" onClick={() => removeReference(reference._id)} aria-label={`Remove ${reference.label}`}>
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
                  {latestVersion?.validation_status && <div>Validation: {pretty(latestVersion.validation_status)}</div>}
                  {(latestVersion?.missing_evidence || []).map((item, idx) => (
                    <div key={`${item}-${idx}`} className="rounded-md border border-amber-200 bg-amber-50 p-2 text-amber-900">
                      {item}
                    </div>
                  ))}
                  {sourceLedger.slice(0, 5).map((source, idx) => {
                    const origin = String(source.source_origin || "");
                    const sourceId = String(source.source_id || "");
                    const isRegisterRow = REGISTER_ORIGINS.has(origin);
                    return (
                      <div key={`${sourceId || idx}-${idx}`} className="rounded-md border p-2">
                        <div className="flex items-start justify-between gap-2">
                          <div>
                            <div className="font-medium">
                              {String(source.source_key || `S${idx + 1}`)} - {String(source.citation || source.label || "Source")}
                            </div>
                            {isRegisterRow && <div className="text-xs text-muted-foreground">{pretty(origin)}</div>}
                            {Array.isArray(source.quality_flags) && source.quality_flags.length > 0 && (
                              <div className="mt-1 text-xs text-muted-foreground">{source.quality_flags.join(", ")}</div>
                            )}
                          </div>
                          {isRegisterRow && !isLocked && sourceId && (
                            <Button
                              size="sm"
                              variant="ghost"
                              onClick={() => excludeRegisterSource(sourceId)}
                              aria-label={`Exclude register source ${String(source.citation || sourceId)}`}
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </Button>
                          )}
                        </div>
                      </div>
                    );
                  })}
                  {(draft?.excluded_register_ids || []).length > 0 && (
                    <div className="text-xs text-muted-foreground">
                      {draft?.excluded_register_ids?.length} register source(s) excluded from this draft.
                    </div>
                  )}
                </CardContent>
              </Card>

              {(approvalBlockers.length > 0 || safetyWarnings.length > 0) && (
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base">
                      <AlertTriangle className="h-4 w-4 text-amber-600" />
                      Legal Safety
                    </CardTitle>
                    <CardDescription>Validation warnings before legal approval or filing</CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3 text-sm">
                    {approvalBlockers.map((item, idx) => (
                      <div key={`blocker-${idx}`} className="rounded-md border border-red-200 bg-red-50 p-2 text-red-900">
                        {item}
                      </div>
                    ))}
                    {safetyWarnings
                      .filter((item) => !approvalBlockers.includes(item))
                      .map((item, idx) => (
                        <div key={`warning-${idx}`} className="rounded-md border border-amber-200 bg-amber-50 p-2 text-amber-900">
                          {item}
                        </div>
                      ))}
                  </CardContent>
                </Card>
              )}
            </aside>
          </div>
        )}
      </div>
    );
  }

  if (kind !== "drafts" && draftType) {
    return (
      <div className="space-y-6 p-6">
        <div>
          <h1 className="text-2xl font-semibold tracking-normal">{draftType.label}</h1>
          <p className="text-sm text-muted-foreground">Create a source-grounded arbitration pleading draft.</p>
        </div>

        <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
          <section className="space-y-4">
            <Card>
              <CardHeader>
                <CardTitle className="text-base">Case Details</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-2">
                <div className="space-y-2">
                  <Label>Project</Label>
                  <Select value={form.project_id} onValueChange={(value) => update("project_id", value)}>
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
                  <Label>Title</Label>
                  <Input value={form.title} onChange={(e) => update("title", e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Party role</Label>
                  <Select value={form.party_role} onValueChange={(value) => update("party_role", value)}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="claimant">Claimant</SelectItem>
                      <SelectItem value="respondent">Respondent</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Dispute type</Label>
                  <Select value={form.dispute_type} onValueChange={(value) => update("dispute_type", value)}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      {DISPUTE_TYPES.map(([value, label]) => (
                        <SelectItem key={value} value={value}>
                          {label}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="space-y-2">
                  <Label>Case workspace (recommended)</Label>
                  <Select value={form.case_id} onValueChange={(value) => update("case_id", value)}>
                    <SelectTrigger>
                      <SelectValue placeholder="Link a case workspace" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="none">None — ungated draft</SelectItem>
                      {cases
                        .filter((item) => !form.project_id || item.project_id === form.project_id)
                        .map((item) => (
                          <SelectItem key={item._id} value={item._id}>
                            {item.title}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">
                    Linking a case applies the matrix readiness gate before generation and approval.
                  </p>
                </div>
                <div className="space-y-2">
                  <Label>Project register sources</Label>
                  <Select value={form.include_registers} onValueChange={(value) => update("include_registers", value)}>
                    <SelectTrigger>
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="yes">Include claims/variations/IPCs/BGs (default)</SelectItem>
                      <SelectItem value="no">Exclude project registers</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Facts, Clause, Relief</CardTitle>
              </CardHeader>
              <CardContent className="space-y-4">
                <Textarea placeholder="Manual facts, chronology, admissions, or assumptions" value={form.manual_facts} onChange={(e) => update("manual_facts", e.target.value)} rows={5} />
                <Textarea placeholder="Arbitration clause / governing provision" value={form.arbitration_clause} onChange={(e) => update("arbitration_clause", e.target.value)} rows={3} />
                <Textarea placeholder="Relief sought / prayer" value={form.relief_sought} onChange={(e) => update("relief_sought", e.target.value)} rows={3} />
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="text-base">Quantum and Initial Evidence</CardTitle>
              </CardHeader>
              <CardContent className="grid gap-4 md:grid-cols-3">
                <div className="space-y-2">
                  <Label>Amount</Label>
                  <Input type="number" value={form.claim_amount} onChange={(e) => update("claim_amount", e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Currency</Label>
                  <Input value={form.currency} onChange={(e) => update("currency", e.target.value)} />
                </div>
                <div className="space-y-2">
                  <Label>Interest rate</Label>
                  <Input type="number" value={form.interest_rate} onChange={(e) => update("interest_rate", e.target.value)} />
                </div>
                <div className="space-y-2 md:col-span-3">
                  <Label>Claim head / defence ground</Label>
                  <Input value={form.claim_head} onChange={(e) => update("claim_head", e.target.value)} />
                </div>
                <div className="space-y-2 md:col-span-3">
                  <Label>Initial evidence label</Label>
                  <Input value={form.reference_label} onChange={(e) => update("reference_label", e.target.value)} />
                </div>
                <div className="space-y-2 md:col-span-3">
                  <Label>Initial evidence note</Label>
                  <Textarea value={form.reference_snippet} onChange={(e) => update("reference_snippet", e.target.value)} rows={3} />
                </div>
              </CardContent>
            </Card>
          </section>

          <aside className="space-y-3 rounded-md border bg-background p-4">
            <h2 className="text-base font-medium">Draft Controls</h2>
            <Button className="w-full" onClick={() => createDraft(false)} disabled={saving}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FilePlus2 className="mr-2 h-4 w-4" />}
              Create Draft
            </Button>
            <Button className="w-full" variant="secondary" onClick={() => createDraft(true)} disabled={saving}>
              <Sparkles className="mr-2 h-4 w-4" />
              Create and Generate
            </Button>
            <p className="text-sm text-muted-foreground">
              Facts without linked support will be marked as [Evidence required] in the generated draft.
            </p>
          </aside>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6 p-6">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div>
          <h1 className="text-2xl font-semibold tracking-normal">Saved Arbitration Drafts</h1>
          <p className="text-sm text-muted-foreground">Create, continue, generate, and export arbitration pleadings.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" asChild>
            <Link to="/arbitration/cases">Case Workspaces</Link>
          </Button>
          <Button asChild>
            <Link to="/arbitration/claim">New SoC</Link>
          </Button>
          <Button variant="outline" asChild>
            <Link to="/arbitration/defence">New SoD</Link>
          </Button>
          <Button variant="outline" asChild>
            <Link to="/arbitration/rejoinder">New Rejoinder</Link>
          </Button>
          <Button variant="outline" asChild>
            <Link to="/arbitration/counterclaim">New Counterclaim</Link>
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Draft Register</CardTitle>
          <CardDescription>Current arbitration pleadings and generated versions</CardDescription>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Title</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Version</TableHead>
                <TableHead className="text-right">Action</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={5}>Loading drafts...</TableCell>
                </TableRow>
              ) : drafts.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={5}>No arbitration drafts found.</TableCell>
                </TableRow>
              ) : (
                drafts.map((item) => (
                  <TableRow key={item._id}>
                    <TableCell className="font-medium">{item.title}</TableCell>
                    <TableCell>{pretty(item.draft_type)}</TableCell>
                    <TableCell>
                      <Badge variant="outline">{item.status}</Badge>
                    </TableCell>
                    <TableCell>{item.current_version || 0}</TableCell>
                    <TableCell className="text-right">
                      <Button size="sm" variant="outline" asChild>
                        <Link to={`/arbitration/drafts/${item._id}`}>Open</Link>
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
};

export default ArbitrationDraftingPage;
