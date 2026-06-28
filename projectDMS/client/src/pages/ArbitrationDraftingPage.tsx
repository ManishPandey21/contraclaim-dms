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
import { Download, FilePlus2, Loader2, RefreshCw, Sparkles } from "lucide-react";
import { toast } from "sonner";
import { enhancedApi } from "@/services/enhanced-api";
import {
  ArbitrationDraft,
  ArbitrationDraftCreatePayload,
  ArbitrationDraftType,
  createArbitrationDraft,
  exportArbitrationDraft,
  generateArbitrationDraft,
  getArbitrationDraft,
  importDefenceParagraphs,
  importSocParagraphs,
  listArbitrationDrafts,
} from "@/services/arbitration-drafting-api";

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
  const [form, setForm] = useState<FormState>(() => initialForm(kind));
  const [pleadingText, setPleadingText] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [generating, setGenerating] = useState(false);

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
    } catch {
      toast.error("Failed to load arbitration draft");
    } finally {
      setLoading(false);
    }
  }, [draftId]);

  useEffect(() => {
    enhancedApi.getProjects().then(setProjects).catch(() => setProjects([]));
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
      const next = await generateArbitrationDraft(draft._id, {});
      setDraft(next);
      toast.success("Draft generated");
    } catch {
      toast.error("Draft generation failed");
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

  if (draftId) {
    const markdown = draft?.latest_version?.full_markdown || "";
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
            <Button onClick={generate} disabled={generating || !draft}>
              {generating ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
              Generate
            </Button>
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

        {loading ? (
          <div className="flex items-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading arbitration draft
          </div>
        ) : (
          <div className="grid gap-4 xl:grid-cols-[1fr_360px]">
            <section className="rounded-md border bg-background p-4">
              <div className="mb-3 flex flex-wrap gap-2">
                <Badge variant="outline">{pretty(draft?.draft_type)}</Badge>
                <Badge variant="neutral">{draft?.status}</Badge>
                <Badge variant="outline">Version {draft?.current_version || 0}</Badge>
              </div>
              <pre className="min-h-[520px] whitespace-pre-wrap rounded-md bg-muted/40 p-4 text-sm leading-6">
                {markdown || "Generate the first version to see the pleading draft here."}
              </pre>
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
                    <Button className="w-full" onClick={importParagraphs} disabled={generating || !pleadingText.trim()}>
                      <RefreshCw className="mr-2 h-4 w-4" />
                      Import Paragraphs
                    </Button>
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader>
                  <CardTitle className="text-base">Evidence Status</CardTitle>
                  <CardDescription>Source ledger and missing proof markers</CardDescription>
                </CardHeader>
                <CardContent className="space-y-3 text-sm">
                  <div>Sources: {draft?.latest_version?.source_ledger?.length || draft?.selected_references?.length || 0}</div>
                  {(draft?.latest_version?.missing_evidence || []).map((item, idx) => (
                    <div key={`${item}-${idx}`} className="rounded-md border border-amber-200 bg-amber-50 p-2 text-amber-900">
                      {item}
                    </div>
                  ))}
                </CardContent>
              </Card>
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
