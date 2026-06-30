import React, { useCallback, useEffect, useState } from "react";
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
import { AlertTriangle, Download, Edit, FileText, Loader2, PlusCircle, Search, ShieldCheck, Trash2 } from "lucide-react";
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
  getInsuranceAlerts,
  getInsuranceSummary,
  getInsuranceTypes,
  updateInsurance,
} from "@/services/insurance-api";
import { enhancedApi } from "@/services/enhanced-api";
import { downloadDocumentFile } from "@/services/documents-api";
import { fmtAmount } from "@/lib/contract-controls-helpers";

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
  remarks: string;
}
const EMPTY: IForm = {
  project_id: "", contract_id: "", contractor_name: "", insurance_type: "", insurance_company: "",
  policy_number: "", sum_insured: "", currency: "INR", date_of_issue: "", date_of_expiry: "",
  document_id: "", remarks: "",
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
  const [items, setItems] = useState<InsuranceDTO[]>([]);
  const [alerts, setAlerts] = useState<InsuranceDTO[]>([]);
  const [summary, setSummary] = useState<InsuranceSummaryDTO | null>(null);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [types, setTypes] = useState<InsuranceTypeDTO[]>([]);
  const [projectFilter, setProjectFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [companyFilter, setCompanyFilter] = useState("");
  const [search, setSearch] = useState("");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<IForm>({ ...EMPTY });
  const [saving, setSaving] = useState(false);

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
    } catch {
      toast.error("Failed to load insurance policies");
    }
  }, [projectFilter, statusFilter, typeFilter, companyFilter, search]);

  useEffect(() => { void load(); }, [load]);

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

  const openCreate = () => {
    setEditingId(null);
    setForm({ ...EMPTY, project_id: projectFilter !== "all" ? projectFilter : "" });
    setDialogOpen(true);
  };
  const openEdit = (b: InsuranceDTO) => {
    setEditingId(b.id);
    setForm({
      project_id: b.project_id || "", contract_id: b.contract_id || "", contractor_name: b.contractor_name || "",
      insurance_type: b.insurance_type || "", insurance_company: b.insurance_company || "",
      policy_number: b.policy_number || "", sum_insured: b.sum_insured != null ? String(b.sum_insured) : "",
      currency: b.currency || "INR",
      date_of_issue: b.date_of_issue ? b.date_of_issue.slice(0, 10) : "",
      date_of_expiry: b.date_of_expiry ? b.date_of_expiry.slice(0, 10) : "",
      document_id: b.document_id || "", remarks: b.remarks || "",
    });
    setDialogOpen(true);
  };

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
    document_id: form.document_id.trim() || undefined,
    remarks: form.remarks.trim() || undefined,
  });

  const submit = async (addAnother = false) => {
    if (!form.project_id || !form.insurance_type || !form.policy_number.trim() || !form.date_of_issue || !form.date_of_expiry) {
      toast.error("Project, insurance type, policy number, issue and expiry dates are required");
      return;
    }
    setSaving(true);
    try {
      const payload = buildPayload();
      if (editingId) await updateInsurance(editingId, payload);
      else await createInsurance(payload);
      await load();
      toast.success(editingId ? "Policy updated" : "Policy created");
      if (addAnother && !editingId) {
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

  const onDownloadFile = async (b: InsuranceDTO) => {
    if (!b.document_id) return;
    try {
      await downloadDocumentFile({ upload_id: b.document_id, filenameFallback: `${b.policy_number || "policy"}.pdf` });
    } catch {
      toast.error("Unable to download file");
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
          <Button variant="outline" size="sm" onClick={() => onExport("csv")}><Download className="mr-2 h-4 w-4" />CSV</Button>
          <Button variant="outline" size="sm" onClick={() => onExport("xlsx")}><Download className="mr-2 h-4 w-4" />Excel</Button>
          <Button variant="outline" size="sm" onClick={() => onExport("pdf")}><Download className="mr-2 h-4 w-4" />PDF</Button>
          <Button onClick={openCreate}><PlusCircle className="mr-2 h-4 w-4" />Add Insurance</Button>
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
            <Select value={projectFilter} onValueChange={setProjectFilter}>
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
                      {b.document_id ? (
                        <Button variant="ghost" size="icon" className="h-8 w-8" title="Download file" onClick={() => onDownloadFile(b)}>
                          <FileText className="h-4 w-4" />
                        </Button>
                      ) : (
                        <span className="text-xs text-muted-foreground">—</span>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" className="h-8 w-8" title="Edit" onClick={() => openEdit(b)}><Edit className="h-4 w-4" /></Button>
                        <AlertDialog>
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
                        </AlertDialog>
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
        <DialogContent className="sm:max-w-lg">
          <DialogHeader>
            <DialogTitle>{editingId ? "Edit Insurance Policy" : "Add Insurance Policy"}</DialogTitle>
            <DialogDescription>Status is derived from the expiry date (active / expiring soon / expired).</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Project</Label>
                <Select value={form.project_id} onValueChange={(v) => setForm({ ...form, project_id: v })}>
                  <SelectTrigger><SelectValue placeholder="Select project" /></SelectTrigger>
                  <SelectContent>{projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}</SelectContent>
                </Select>
              </div>
              <div><Label>Contract ID</Label><Input value={form.contract_id} onChange={(e) => setForm({ ...form, contract_id: e.target.value })} /></div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><Label>Contractor</Label><Input value={form.contractor_name} onChange={(e) => setForm({ ...form, contractor_name: e.target.value })} /></div>
              <div>
                <Label>Insurance type</Label>
                <Select value={form.insurance_type} onValueChange={(v) => setForm({ ...form, insurance_type: v })}>
                  <SelectTrigger><SelectValue placeholder="Select type" /></SelectTrigger>
                  <SelectContent>{types.map((t) => <SelectItem key={t.id} value={t.name}>{t.name}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><Label>Policy number</Label><Input value={form.policy_number} onChange={(e) => setForm({ ...form, policy_number: e.target.value })} /></div>
              <div><Label>Insurance company</Label><Input value={form.insurance_company} onChange={(e) => setForm({ ...form, insurance_company: e.target.value })} /></div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div><Label>Sum insured</Label><Input type="number" value={form.sum_insured} onChange={(e) => setForm({ ...form, sum_insured: e.target.value })} /></div>
              <div><Label>Date of issue</Label><Input type="date" value={form.date_of_issue} onChange={(e) => setForm({ ...form, date_of_issue: e.target.value })} /></div>
              <div><Label>Date of expiry</Label><Input type="date" value={form.date_of_expiry} onChange={(e) => setForm({ ...form, date_of_expiry: e.target.value })} /></div>
            </div>
            <div><Label>Document ID (uploaded policy file)</Label><Input value={form.document_id} onChange={(e) => setForm({ ...form, document_id: e.target.value })} placeholder="upload_id of the policy PDF/JPG/PNG" /></div>
            <div><Label>Remarks</Label><Textarea value={form.remarks} onChange={(e) => setForm({ ...form, remarks: e.target.value })} rows={2} /></div>
          </div>
          <DialogFooter className="flex-wrap gap-2">
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>Cancel</Button>
            {!editingId && (
              <Button variant="outline" onClick={() => submit(true)} disabled={saving}>Save &amp; Add New</Button>
            )}
            <Button onClick={() => submit(false)} disabled={saving}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlusCircle className="mr-2 h-4 w-4" />}
              {editingId ? "Save changes" : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default InsuranceRegisterPage;
