import React, { useCallback, useEffect, useRef, useState } from "react";
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
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { AlertTriangle, Download, Edit, Eye, FileText, Loader2, PlusCircle, Search, ShieldCheck, Trash2, Upload, X } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import {
  InsuranceDTO,
  InsurancePayload,
  InsuranceSummaryDTO,
  InsuranceTypeDTO,
  createInsurance,
  deleteInsurance,
  exportInsurance,
  getInsurance,
  getInsuranceById,
  getInsuranceAlerts,
  getInsuranceSummary,
  getInsuranceTypes,
  updateInsurance,
  uploadInsuranceDocument,
} from "@/services/insurance-api";
import { listContractMaster } from "@/services/contract-master-api";
import { enhancedApi } from "@/services/enhanced-api";
import { fmtAmount } from "@/lib/contract-controls-helpers";
import EntityDocumentLinks from "@/components/document-links/EntityDocumentLinks";
import {
  INSURANCE_DOCUMENT_RELATIONSHIP_ROLES,
  searchLinkableDocuments,
} from "@/services/document-relationships-api";
import type { DocumentItem } from "@/services/documents-api";
import type { InsuranceUploadRole } from "@/services/insurance-api";
import useHasPermission from "@/hooks/useHasPermission";
import { useRegisterProjectScope } from "@/hooks/useRegisterProjectScope";
import { scopeRefusalMessage } from "@/services/active-scope";

const STATUSES = ["active", "expiring_soon", "expired"];
const fmtDate = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");
const toISO = (d: string) => (d ? new Date(d).toISOString() : undefined);

const statusLabel = (s?: string | null) =>
  s === "active" ? "Active" : s === "expiring_soon" ? "Expiring Soon" : s === "expired" ? "Expired" : "—";
const statusColor = (s?: string | null) =>
  s === "active"
    ? "bg-green-100 text-green-800 border-green-200"
    : s === "expiring_soon"
      ? "bg-amber-100 text-amber-800 border-amber-200"
      : s === "expired"
        ? "bg-red-100 text-red-800 border-red-200"
        : "bg-gray-100 text-gray-700";

// "18 Days Left" / "Expires today" / "Expired 4 days ago".
const countdownText = (days?: number | null): string => {
  if (days == null) return "—";
  if (days > 0) return `${days} Day${days === 1 ? "" : "s"} Left`;
  if (days === 0) return "Expires today";
  return `Expired ${Math.abs(days)} Day${days === -1 ? "" : "s"} ago`;
};

interface IForm {
  project_id: string;
  contract_id: string;
  contractor_name: string;
  insurance_type: string;
  insurance_company: string;
  policy_number: string;
  sum_insured: string;
  currency: string;
  date_of_issue: string;
  date_of_expiry: string;
  document_id: string;
  document_name: string;
  document_content_type: string;
  remarks: string;
}
const EMPTY: IForm = {
  project_id: "", contract_id: "", contractor_name: "", insurance_type: "", insurance_company: "",
  policy_number: "", sum_insured: "", currency: "INR", date_of_issue: "", date_of_expiry: "",
  document_id: "", document_name: "", document_content_type: "", remarks: "",
};

const Stat: React.FC<{ label: string; value: string; cls?: string }> = ({ label, value, cls }) => (
  <Card className="flex flex-col">
    <CardHeader>
      <CardDescription className="break-words leading-tight">{label}</CardDescription>
    </CardHeader>
    <CardContent className="mt-auto">
      <CardTitle className={`text-2xl break-words leading-tight ${cls || ""}`}>{value}</CardTitle>
    </CardContent>
  </Card>
);

const InsuranceRegisterPage: React.FC = () => {
  // Deep link from a Document's Linked Records: /insurance?insurance_id=...
  const [searchParams] = useSearchParams();
  const deepLinkId = searchParams.get("insurance_id");
  const openedDeepLink = useRef<string | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [items, setItems] = useState<InsuranceDTO[]>([]);
  const [alerts, setAlerts] = useState<InsuranceDTO[]>([]);
  const [summary, setSummary] = useState<InsuranceSummaryDTO | null>(null);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [types, setTypes] = useState<InsuranceTypeDTO[]>([]);
  // CL-4A: the navbar project pins this filter (useRegisterProjectScope).
  const { projectFilter, setProjectFilter, projectLocked, tenantLoading } = useRegisterProjectScope();
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [companyFilter, setCompanyFilter] = useState("");
  const [search, setSearch] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<IForm>({ ...EMPTY });
  const [saving, setSaving] = useState(false);
  const [pendingFile, setPendingFile] = useState<File | null>(null);
  const [pendingRole, setPendingRole] = useState<InsuranceUploadRole>("policy");
  // Create-time evidence can come from a new upload OR an existing Document.
  const [linkExisting, setLinkExisting] = useState(false);
  const [existingQuery, setExistingQuery] = useState("");
  const [existingResults, setExistingResults] = useState<DocumentItem[]>([]);
  const [selectedExisting, setSelectedExisting] = useState<DocumentItem | null>(null);
  // Contracts for the selected project drive the Contract ID dropdown + auto-fill.
  const [contracts, setContracts] = useState<{ contract_id: string; contractor_name?: string | null }[]>([]);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const canEdit = useHasPermission("dms.insurance.edit");
  const canCreate = useHasPermission("dms.insurance.create");
  const canDelete = useHasPermission("dms.insurance.delete");
  const canExport = useHasPermission("dms.insurance.export");

  const load = useCallback(async () => {
    try {
      const params: Record<string, string> = {};
      if (projectFilter !== "all") params.project_id = projectFilter;
      if (statusFilter !== "all") params.status = statusFilter;
      if (typeFilter !== "all") params.type = typeFilter;
      if (companyFilter.trim()) params.company = companyFilter.trim();
      if (search.trim()) params.q = search.trim();
      const scoped = projectFilter !== "all" ? { project_id: projectFilter } : undefined;
      setItems(await getInsurance(params));
      setSummary(await getInsuranceSummary(scoped));
      setAlerts(await getInsuranceAlerts(scoped));
    } catch (error) {
      toast.error(scopeRefusalMessage(error, "insurance policy") || "Failed to load insurance policies");
    } finally {
      setLoaded(true);
    }
  }, [projectFilter, statusFilter, typeFilter, companyFilter, search]);

  useEffect(() => { if (!tenantLoading) void load(); }, [load, tenantLoading]);

  // Server-side canonical search. No client-side filtering of a preloaded list:
  // the Document library is far larger than any page we could hold.
  useEffect(() => {
    if (!linkExisting || !existingQuery.trim()) {
      setExistingResults([]);
      return;
    }
    let cancelled = false;
    const timer = window.setTimeout(() => {
      void searchLinkableDocuments({
        q: existingQuery.trim(),
        project_id: form.project_id || undefined,
      })
        .then((rows) => { if (!cancelled) setExistingResults(rows); })
        .catch(() => { if (!cancelled) setExistingResults([]); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [linkExisting, existingQuery, form.project_id]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const ps = await enhancedApi.getProjects();
        if (active) setProjects((ps || []).map((p: any) => ({ id: String(p._id || p.id || ""), name: p.name || "Project" })));
      } catch { /* optional */ }
      try {
        const ts = await getInsuranceTypes();
        if (active) setTypes(ts);
      } catch { /* optional */ }
    })();
    return () => { active = false; };
  }, []);

  // Load the project's contracts whenever the dialog's project changes.
  useEffect(() => {
    if (!dialogOpen || !form.project_id) {
      setContracts([]);
      return;
    }
    let active = true;
    listContractMaster({ project_id: form.project_id })
      .then((cs) => {
        if (active) setContracts(cs.map((c) => ({ contract_id: c.contract_id, contractor_name: c.contractor_name })));
      })
      .catch(() => { if (active) setContracts([]); });
    return () => { active = false; };
  }, [dialogOpen, form.project_id]);

  const onContractChange = (contractId: string) => {
    const c = contracts.find((x) => x.contract_id === contractId);
    setForm((f) => ({ ...f, contract_id: contractId, contractor_name: c?.contractor_name || f.contractor_name }));
  };

  const onPickFile = () => fileInputRef.current?.click();

  const onFileSelected = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = ""; // allow re-selecting the same file
    if (!file) return;
    const ext = file.name.split(".").pop()?.toLowerCase() || "";
    if (!["pdf", "jpg", "jpeg", "png"].includes(ext)) {
      toast.error("Only PDF, JPG, or PNG files are allowed");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      toast.error("File exceeds the 20 MB limit");
      return;
    }
    setPendingFile(file);
  };

  const clearFile = () => {
    setPendingFile(null);
    setPendingRole("policy");
  };

  const resetExistingSelection = () => {
    setLinkExisting(false);
    setExistingQuery("");
    setExistingResults([]);
    setSelectedExisting(null);
  };

  const openCreate = () => {
    setEditingId(null);
    setPendingFile(null);
    setPendingRole("policy");
    resetExistingSelection();
    setForm({ ...EMPTY, project_id: projectFilter !== "all" ? projectFilter : "" });
    setDialogOpen(true);
  };
  const openEdit = (b: InsuranceDTO) => {
    setEditingId(b.id);
    setPendingFile(null);
    setPendingRole("policy");
    setForm({
      project_id: b.project_id || "", contract_id: b.contract_id || "", contractor_name: b.contractor_name || "",
      insurance_type: b.insurance_type || "", insurance_company: b.insurance_company || "",
      policy_number: b.policy_number || "", sum_insured: b.sum_insured != null ? String(b.sum_insured) : "",
      currency: b.currency || "INR",
      date_of_issue: b.date_of_issue ? b.date_of_issue.slice(0, 10) : "",
      date_of_expiry: b.date_of_expiry ? b.date_of_expiry.slice(0, 10) : "",
      document_id: b.document_id || "", document_name: b.document_name || "",
      document_content_type: b.document_content_type || "", remarks: b.remarks || "",
    });
    setDialogOpen(true);
  };

  useEffect(() => {
    if (!deepLinkId || !loaded || openedDeepLink.current === deepLinkId) return;
    openedDeepLink.current = deepLinkId;
    const local = items.find((item) => item.id === deepLinkId);
    if (local) {
      openEdit(local);
      return;
    }
    void getInsuranceById(deepLinkId)
      .then(openEdit)
      .catch((error) => toast.error(scopeRefusalMessage(error, "insurance policy") || "Linked insurance policy could not be opened"));
  }, [deepLinkId, items, loaded]);

  const buildPayload = (): InsurancePayload => ({
    project_id: form.project_id,
    insurance_type: form.insurance_type,
    policy_number: form.policy_number.trim(),
    contract_id: form.contract_id.trim() || undefined,
    contractor_name: form.contractor_name.trim() || undefined,
    insurance_company: form.insurance_company.trim() || undefined,
    sum_insured: form.sum_insured ? Number(form.sum_insured) : undefined,
    currency: form.currency || "INR",
    date_of_issue: toISO(form.date_of_issue),
    date_of_expiry: toISO(form.date_of_expiry),
    remarks: form.remarks.trim() || undefined,
  });

  const submit = async (addAnother = false) => {
    if (!form.project_id || !form.insurance_type || !form.policy_number.trim() || !form.date_of_issue || !form.date_of_expiry) {
      toast.error("Project, insurance type, policy number, issue and expiry dates are required");
      return;
    }
    if (!editingId && !pendingFile && !selectedExisting) {
      toast.error("Upload the insurance document or link an existing Document");
      return;
    }
    setSaving(true);
    try {
      const payload = buildPayload();
      const existingDocumentId = selectedExisting
        ? String(selectedExisting._id || selectedExisting.id || "")
        : "";
      if (!editingId && existingDocumentId) {
        payload.existing_document_links = [
          { document_id: existingDocumentId, relationship_role: pendingRole },
        ];
      }
      const saved = editingId
        ? await updateInsurance(editingId, payload)
        : await createInsurance(payload);
      const savedId = editingId || saved.id;
      if (pendingFile) {
        try {
          await uploadInsuranceDocument(savedId, pendingFile, pendingRole);
          setPendingFile(null);
          setPendingRole("policy");
        } catch (uploadError: any) {
          setEditingId(savedId);
          toast.error(uploadError?.response?.data?.detail || "Policy saved, but Document upload failed; retry the upload");
          await load();
          return;
        }
      }
      await load();
      toast.success(editingId ? "Policy updated" : "Policy created");
      if (addAnother && !editingId) {
        setPendingFile(null);
        // The next policy is a different policy: it must not silently inherit
        // the previous one's evidence.
        resetExistingSelection();
        setForm({ ...EMPTY, project_id: form.project_id, contract_id: form.contract_id, contractor_name: form.contractor_name });
      } else {
        setDialogOpen(false);
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to save policy");
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async (b: InsuranceDTO) => {
    try {
      await deleteInsurance(b.id);
      await load();
      toast.success("Policy deleted");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to delete policy");
    }
  };

  const onExport = async (format: "csv" | "xlsx" | "pdf") => {
    try {
      const blob = await exportInsurance(format, projectFilter !== "all" ? { project_id: projectFilter } : undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `insurance-register.${format}`; a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Export failed");
    }
  };

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <ShieldCheck className="h-6 w-6 text-blue-600" />
          <div>
            <h1 className="text-2xl font-bold">Insurance Management</h1>
            <p className="text-sm text-muted-foreground">Contractor insurance policies, validity tracking and expiry alerts.</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {canExport && <Button variant="outline" size="sm" onClick={() => onExport("csv")}><Download className="mr-2 h-4 w-4" />CSV</Button>}
          {canExport && <Button variant="outline" size="sm" onClick={() => onExport("xlsx")}><Download className="mr-2 h-4 w-4" />Excel</Button>}
          {canExport && <Button variant="outline" size="sm" onClick={() => onExport("pdf")}><Download className="mr-2 h-4 w-4" />PDF</Button>}
          {canCreate && <Button onClick={openCreate}><PlusCircle className="mr-2 h-4 w-4" />Add Insurance</Button>}
        </div>
      </div>

      {summary && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
          <Stat label="Total Policies" value={String(summary.total)} />
          <Stat label="Active" value={String(summary.active)} cls="text-green-600" />
          <Stat label="Expiring ≤30d" value={String(summary.expiring_soon)} cls="text-amber-600" />
          <Stat label="Expired" value={String(summary.expired)} cls="text-red-700" />
          <Stat label="Missing" value={String(summary.missing)} cls="text-slate-600" />
        </div>
      )}

      {alerts.length > 0 && (
        <Card className="border-amber-300 bg-amber-50">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base text-amber-800">
              <AlertTriangle className="h-5 w-5" />
              {alerts.length} insurance polic{alerts.length > 1 ? "ies" : "y"} need attention
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2 pt-0">
            {alerts.map((b) => (
              <button
                key={b.id}
                type="button"
                onClick={() => openEdit(b)}
                className="rounded-md border border-amber-300 bg-white px-3 py-1.5 text-left text-sm hover:bg-amber-100"
                title={`Expiry ${fmtDate(b.date_of_expiry)}`}
              >
                <span className="font-medium">{b.policy_number || b.insurance_type}</span>
                <span className="ml-2 text-amber-700">{countdownText(b.days_remaining)}</span>
              </button>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Insurance Policies</CardTitle>
          <div className="flex flex-wrap gap-3 pt-3">
            <div className="relative">
              <Search className="absolute left-2 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                placeholder="Search contract, contractor, policy, type, company"
                className="w-72 pl-8"
                value={search}
                onChange={(e) => setSearch(e.target.value)}
              />
            </div>
            <Select value={projectFilter} onValueChange={setProjectFilter} disabled={projectLocked}>
              <SelectTrigger className="w-48"><SelectValue placeholder="Project" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All projects</SelectItem>
                {projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="w-56"><SelectValue placeholder="Insurance type" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All types</SelectItem>
                {types.map((t) => <SelectItem key={t.id} value={t.name}>{t.name}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-44"><SelectValue placeholder="Status" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {STATUSES.map((s) => <SelectItem key={s} value={s}>{statusLabel(s)}</SelectItem>)}
              </SelectContent>
            </Select>
            <Input
              placeholder="Insurance company"
              className="w-48"
              value={companyFilter}
              onChange={(e) => setCompanyFilter(e.target.value)}
            />
          </div>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Contract ID</TableHead>
                <TableHead>Contractor</TableHead>
                <TableHead>Insurance Type</TableHead>
                <TableHead>Policy No.</TableHead>
                <TableHead>Issue</TableHead>
                <TableHead>Expiry</TableHead>
                <TableHead>Validity</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Company</TableHead>
                <TableHead>Sum Insured</TableHead>
                <TableHead>Uploaded By</TableHead>
                <TableHead>File</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.length === 0 ? (
                <TableRow><TableCell colSpan={13} className="py-8 text-center text-muted-foreground">No insurance policies yet.</TableCell></TableRow>
              ) : (
                items.map((b) => (
                  <TableRow key={b.id} className={b.status === "expired" ? "bg-red-50" : b.status === "expiring_soon" ? "bg-amber-50" : undefined}>
                    <TableCell className="font-mono text-xs">{b.contract_id || "—"}</TableCell>
                    <TableCell className="text-xs">{b.contractor_name || "—"}</TableCell>
                    <TableCell className="text-xs">{b.insurance_type || "—"}</TableCell>
                    <TableCell className="font-mono text-xs">{b.policy_number || "—"}</TableCell>
                    <TableCell>{fmtDate(b.date_of_issue)}</TableCell>
                    <TableCell>{fmtDate(b.date_of_expiry)}</TableCell>
                    <TableCell className="text-xs">{countdownText(b.days_remaining)}</TableCell>
                    <TableCell><Badge variant="outline" className={statusColor(b.status)}>{statusLabel(b.status)}</Badge></TableCell>
                    <TableCell className="text-xs">{b.insurance_company || "—"}</TableCell>
                    <TableCell>{b.sum_insured != null ? fmtAmount(b.sum_insured, b.currency) : "—"}</TableCell>
                    <TableCell className="text-xs">{b.created_by_name || "—"}</TableCell>
                    <TableCell>
                      {b.linked_document_ids?.length ? (
                        <Link
                          to={`/documentviewer/${b.linked_document_ids[0]}`}
                          className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
                          title="Open linked Document"
                        >
                          <FileText className="h-3.5 w-3.5" />
                          Documents ({b.linked_document_ids.length})
                        </Link>
                      ) : b.document_id
                        ? <Badge variant="outline">Legacy review</Badge>
                        : <span className="text-xs text-muted-foreground">No linked Document</span>}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" className="h-8 w-8" title={canEdit ? "Edit" : "View"} onClick={() => openEdit(b)}>{canEdit ? <Edit className="h-4 w-4" /> : <Eye className="h-4 w-4" />}</Button>
                        {canDelete && <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8" title="Delete"><Trash2 className="h-4 w-4 text-destructive" /></Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>Delete insurance policy?</AlertDialogTitle>
                              <AlertDialogDescription>
                                This permanently deletes policy &quot;{b.policy_number || b.insurance_type}&quot;. This cannot be undone.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>Cancel</AlertDialogCancel>
                              <AlertDialogAction onClick={() => onDelete(b)}>Delete</AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>}
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      {/* Create / edit */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{editingId ? (canEdit ? "Edit Insurance Policy" : "View Insurance Policy") : "Add Insurance Policy"}</DialogTitle>
            <DialogDescription>Status is derived from the expiry date (active / expiring soon / expired).</DialogDescription>
          </DialogHeader>
          <fieldset className="space-y-3" disabled={Boolean(editingId && !canEdit)}>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Project</Label>
                <Select value={form.project_id} onValueChange={(v) => setForm({ ...form, project_id: v })}>
                  <SelectTrigger aria-label="Project"><SelectValue placeholder="Select project" /></SelectTrigger>
                  <SelectContent>{projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div>
                <Label>Contract ID</Label>
                {contracts.length > 0 ? (
                  <Select value={form.contract_id} onValueChange={onContractChange}>
                    <SelectTrigger><SelectValue placeholder="Select contract" /></SelectTrigger>
                    <SelectContent>
                      {contracts.map((c) => (
                        <SelectItem key={c.contract_id} value={c.contract_id}>
                          {c.contract_id}{c.contractor_name ? ` — ${c.contractor_name}` : ""}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                ) : (
                  <Input
                    value={form.contract_id}
                    onChange={(e) => setForm({ ...form, contract_id: e.target.value })}
                    placeholder={form.project_id ? "No contracts — type ID" : "Select a project first"}
                  />
                )}
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Contractor</Label>
                <Input
                  value={form.contractor_name}
                  onChange={(e) => setForm({ ...form, contractor_name: e.target.value })}
                  placeholder="Auto-filled from contract"
                />
              </div>
              <div>
                <Label>Insurance type</Label>
                <Select value={form.insurance_type} onValueChange={(v) => setForm({ ...form, insurance_type: v })}>
                  <SelectTrigger aria-label="Insurance type"><SelectValue placeholder="Select type" /></SelectTrigger>
                  <SelectContent>{types.map((t) => <SelectItem key={t.id} value={t.name}>{t.name}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><Label>Policy number</Label><Input aria-label="Policy number" value={form.policy_number} onChange={(e) => setForm({ ...form, policy_number: e.target.value })} /></div>
              <div><Label>Insurance company</Label><Input value={form.insurance_company} onChange={(e) => setForm({ ...form, insurance_company: e.target.value })} /></div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div><Label>Sum insured</Label><Input type="number" value={form.sum_insured} onChange={(e) => setForm({ ...form, sum_insured: e.target.value })} /></div>
              <div><Label>Date of issue</Label><Input aria-label="Date of issue" type="date" value={form.date_of_issue} onChange={(e) => setForm({ ...form, date_of_issue: e.target.value })} /></div>
              <div><Label>Date of expiry</Label><Input aria-label="Date of expiry" type="date" value={form.date_of_expiry} onChange={(e) => setForm({ ...form, date_of_expiry: e.target.value })} /></div>
            </div>
            {(!editingId || canEdit) && <div>
              <Label>Policy document (PDF, JPG, PNG · max 20MB)</Label>
              <div className="mb-2">
                <Label htmlFor="insurance-upload-role">Document relationship role</Label>
                <select
                  id="insurance-upload-role"
                  aria-label="Upload relationship role"
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={pendingRole}
                  onChange={(event) => setPendingRole(event.target.value as InsuranceUploadRole)}
                >
                  {INSURANCE_DOCUMENT_RELATIONSHIP_ROLES.map((role) => (
                    <option key={role.value} value={role.value}>
                      {role.label}
                    </option>
                  ))}
                </select>
              </div>
              <input
                ref={fileInputRef}
                type="file"
                accept=".pdf,.jpg,.jpeg,.png"
                className="hidden"
                onChange={onFileSelected}
              />
              {pendingFile ? (
                <div className="flex items-center gap-2 rounded-md border p-2">
                  <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="flex-1 truncate text-sm">{pendingFile.name}</span>
                  <Button type="button" variant="ghost" size="sm" onClick={onPickFile}>Replace</Button>
                  <Button type="button" variant="ghost" size="icon" className="h-7 w-7" onClick={clearFile}>
                    <X className="h-4 w-4" />
                  </Button>
                </div>
              ) : (
                <Button type="button" variant="outline" className="w-full" onClick={onPickFile}>
                  <Upload className="mr-2 h-4 w-4" />
                  Select policy file
                </Button>
              )}
            </div>}
            {editingId && form.document_id && (
              <p className="text-xs text-muted-foreground">
                A legacy Insurance file is preserved for canonicalization or manual review.
              </p>
            )}
            {editingId ? (
              <EntityDocumentLinks
                targetType="insurance"
                targetId={editingId}
                organizationId={items.find((item) => item.id === editingId)?.organization_id}
                projectId={form.project_id}
                roles={INSURANCE_DOCUMENT_RELATIONSHIP_ROLES}
                defaultRole="policy"
                canManage={canEdit}
              />
            ) : (
              <div className="space-y-2">
                {!linkExisting ? (
                  <Button
                    type="button"
                    variant="outline"
                    className="w-full"
                    onClick={() => { setLinkExisting(true); clearFile(); }}
                  >
                    <Search className="mr-2 h-4 w-4" />
                    Link existing Document
                  </Button>
                ) : (
                  <div className="space-y-2 rounded-md border p-2">
                    <div className="flex items-center justify-between">
                      <Label htmlFor="insurance-existing-search">Search existing Documents</Label>
                      <Button type="button" variant="ghost" size="sm" onClick={resetExistingSelection}>
                        Upload a new file instead
                      </Button>
                    </div>
                    <Input
                      id="insurance-existing-search"
                      aria-label="Search existing Documents"
                      value={existingQuery}
                      onChange={(event) => setExistingQuery(event.target.value)}
                      placeholder="Search by number, subject or filename"
                    />
                    {selectedExisting ? (
                      <div className="flex items-center gap-2 rounded-md border p-2">
                        <FileText className="h-4 w-4 shrink-0 text-muted-foreground" />
                        <span className="flex-1 truncate text-sm">
                          {selectedExisting.filename || selectedExisting.subject || "Document"}
                        </span>
                        <Button asChild variant="ghost" size="sm">
                          <Link
                            to={`/documentviewer/${selectedExisting._id || selectedExisting.id}`}
                            aria-label={`View ${selectedExisting.filename || selectedExisting.subject || "Document"}`}
                          >
                            <Eye className="h-4 w-4" />
                          </Link>
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="h-7 w-7"
                          onClick={() => setSelectedExisting(null)}
                        >
                          <X className="h-4 w-4" />
                        </Button>
                      </div>
                    ) : (
                      <ul className="max-h-48 space-y-1 overflow-y-auto">
                        {existingResults.map((row) => {
                          const label = row.filename || row.subject || "Document";
                          return (
                            <li key={String(row._id || row.id)}>
                              <Button
                                type="button"
                                variant="ghost"
                                className="w-full justify-start"
                                aria-label={`Select ${label}`}
                                onClick={() => setSelectedExisting(row)}
                              >
                                <FileText className="mr-2 h-4 w-4 shrink-0" />
                                <span className="truncate">{label}</span>
                              </Button>
                            </li>
                          );
                        })}
                      </ul>
                    )}
                  </div>
                )}
                <p className="text-sm text-muted-foreground">
                  Additional Documents can be linked once the policy is saved.
                </p>
              </div>
            )}
            <div><Label>Remarks</Label><Textarea value={form.remarks} onChange={(e) => setForm({ ...form, remarks: e.target.value })} rows={2} /></div>
          </fieldset>
          <DialogFooter className="flex-wrap gap-2">
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>Cancel</Button>
            {!editingId && (
              <Button variant="outline" onClick={() => submit(true)} disabled={saving}>Save &amp; Add New</Button>
            )}
            {(!editingId || canEdit) && <Button onClick={() => submit(false)} disabled={saving}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlusCircle className="mr-2 h-4 w-4" />}
              {editingId ? "Save changes" : "Save"}
            </Button>}
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default InsuranceRegisterPage;
