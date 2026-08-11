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
import { AlertTriangle, CalendarPlus, Download, Edit, Landmark, Loader2, PlusCircle, Unlock, Upload } from "lucide-react";
import { toast } from "sonner";
import CsvImportDialog from "@/components/registers/CsvImportDialog";
import {
  BGDTO,
  BGPayload,
  BGSummaryDTO,
  createBG,
  downloadBGImportTemplate,
  exportBGs,
  extendBG,
  getBGs,
  getBGAlerts,
  getBGSummary,
  importBGsCsv,
  previewBGsCsv,
  releaseBG,
  updateBG,
} from "@/services/bank-guarantees-api";
import { enhancedApi } from "@/services/enhanced-api";
import { getContractMasterForProject } from "@/services/contract-master-api";
import { bgStatusColor, bgStatusLabel, bgTypeLabel, bgAlertText, fmtAmount } from "@/lib/contract-controls-helpers";

const BG_TYPES = ["performance", "mobilisation_advance", "plant_advance", "retention", "additional_performance", "security_deposit", "other"];
const BG_STATUSES = ["draft", "submitted", "valid", "extension_required", "extended", "expired", "released", "encashment_under_process", "encashed"];
const fmtDate = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");
const toISO = (d: string) => (d ? new Date(d).toISOString() : undefined);

interface BForm {
  project_id: string; bg_type: string; bg_number: string; issuing_bank: string; branch: string;
  bg_amount: string; currency: string; conversion_rate: string; submission_date: string; contractual_required_up_to: string;
  bg_expiry_date: string; claim_expiry_date: string; bg_status: string; remarks: string;
}
const EMPTY: BForm = {
  project_id: "", bg_type: "performance", bg_number: "", issuing_bank: "", branch: "",
  bg_amount: "", currency: "INR", conversion_rate: "1", submission_date: "", contractual_required_up_to: "",
  bg_expiry_date: "", claim_expiry_date: "", bg_status: "valid", remarks: "",
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

const BankGuaranteeRegisterPage: React.FC = () => {
  const [items, setItems] = useState<BGDTO[]>([]);
  const [alerts, setAlerts] = useState<BGDTO[]>([]);
  const [summary, setSummary] = useState<BGSummaryDTO | null>(null);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [projectFilter, setProjectFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [typeFilter, setTypeFilter] = useState("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [csvOpen, setCsvOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [form, setForm] = useState<BForm>({ ...EMPTY });
  const [saving, setSaving] = useState(false);

  const [extendBg, setExtendBg] = useState<BGDTO | null>(null);
  const [extForm, setExtForm] = useState({ revised_expiry_date: "", revised_claim_expiry_date: "", revised_required_up_to: "", extension_letter_reference: "", remarks: "" });
  // Contract currencies for the form's project (award-fixed rates, read-only here).
  const [formBaseCurrency, setFormBaseCurrency] = useState("INR");
  const [formCurrencies, setFormCurrencies] = useState<{ currency: string; conversion_rate: number }[]>([]);

  const load = useCallback(async () => {
    try {
      const params: Record<string, string> = {};
      if (projectFilter !== "all") params.project_id = projectFilter;
      if (statusFilter !== "all") params.status = statusFilter;
      if (typeFilter !== "all") params.type = typeFilter;
      const scoped = projectFilter !== "all" ? { project_id: projectFilter } : undefined;
      setItems(await getBGs(params));
      setSummary(await getBGSummary(scoped));
      setAlerts(await getBGAlerts(scoped));
    } catch {
      toast.error("Failed to load bank guarantees");
    }
  }, [projectFilter, statusFilter, typeFilter]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const ps = await enhancedApi.getProjects();
        if (active) setProjects((ps || []).map((p: any) => ({ id: String(p._id || p.id || ""), name: p.name || "Project" })));
      } catch { /* optional */ }
    })();
    return () => { active = false; };
  }, []);
  // When the dialog is open, pull the contract's currencies so the BG currency
  // can be picked from them and the rate auto-filled (fixed at award).
  useEffect(() => {
    if (!dialogOpen || !form.project_id) { setFormCurrencies([]); setFormBaseCurrency("INR"); return; }
    let active = true;
    (async () => {
      try {
        const cm = await getContractMasterForProject(form.project_id);
        if (!active) return;
        setFormBaseCurrency(cm?.currency || "INR");
        setFormCurrencies((cm?.contract_currencies || []).map((c) => ({ currency: c.currency, conversion_rate: c.conversion_rate })));
      } catch { if (active) { setFormCurrencies([]); setFormBaseCurrency("INR"); } }
    })();
    return () => { active = false; };
  }, [dialogOpen, form.project_id]);

  const onBgCurrencyChange = (cur: string) => {
    const match = formCurrencies.find((c) => c.currency === cur);
    const rate = cur === formBaseCurrency ? 1 : (match?.conversion_rate ?? (Number(form.conversion_rate) || 1));
    setForm((f) => ({ ...f, currency: cur, conversion_rate: String(rate) }));
  };

  const openCreate = () => {
    setEditingId(null);
    setForm({ ...EMPTY, project_id: projectFilter !== "all" ? projectFilter : "" });
    setDialogOpen(true);
  };
  const openEdit = (b: BGDTO) => {
    setEditingId(b.id);
    setForm({
      project_id: b.project_id || "", bg_type: b.bg_type, bg_number: b.bg_number || "",
      issuing_bank: b.issuing_bank || "", branch: b.branch || "",
      bg_amount: b.bg_amount != null ? String(b.bg_amount) : "", currency: b.currency || "INR",
      conversion_rate: b.conversion_rate != null ? String(b.conversion_rate) : "1",
      submission_date: b.submission_date ? b.submission_date.slice(0, 10) : "",
      contractual_required_up_to: b.contractual_required_up_to ? b.contractual_required_up_to.slice(0, 10) : "",
      bg_expiry_date: b.bg_expiry_date ? b.bg_expiry_date.slice(0, 10) : "",
      claim_expiry_date: b.claim_expiry_date ? b.claim_expiry_date.slice(0, 10) : "",
      bg_status: b.bg_status, remarks: b.remarks || "",
    });
    setDialogOpen(true);
  };

  const submit = async () => {
    if (!form.project_id || !form.bg_number.trim()) {
      toast.error("Project and BG number are required");
      return;
    }
    setSaving(true);
    try {
      const payload: BGPayload = {
        project_id: form.project_id, bg_type: form.bg_type as any, bg_number: form.bg_number.trim(),
        issuing_bank: form.issuing_bank || undefined, branch: form.branch || undefined,
        bg_amount: form.bg_amount ? Number(form.bg_amount) : undefined, currency: form.currency || "INR",
        conversion_rate: form.conversion_rate ? Number(form.conversion_rate) : undefined,
        submission_date: toISO(form.submission_date), contractual_required_up_to: toISO(form.contractual_required_up_to),
        bg_expiry_date: toISO(form.bg_expiry_date), claim_expiry_date: toISO(form.claim_expiry_date),
        bg_status: form.bg_status as any, remarks: form.remarks || undefined,
      };
      if (editingId) await updateBG(editingId, payload);
      else await createBG(payload);
      await load();
      toast.success(editingId ? "BG updated" : "BG created");
      setDialogOpen(false);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to save BG");
    } finally {
      setSaving(false);
    }
  };

  const onExtend = async () => {
    if (!extendBg || !extForm.revised_expiry_date) {
      toast.error("Revised expiry date is required");
      return;
    }
    setSaving(true);
    try {
      await extendBG(extendBg.id, {
        revised_expiry_date: new Date(extForm.revised_expiry_date).toISOString(),
        revised_claim_expiry_date: toISO(extForm.revised_claim_expiry_date),
        revised_required_up_to: toISO(extForm.revised_required_up_to),
        extension_letter_reference: extForm.extension_letter_reference || undefined,
        remarks: extForm.remarks || undefined,
      });
      toast.success("BG extended");
      setExtendBg(null);
      setExtForm({ revised_expiry_date: "", revised_claim_expiry_date: "", revised_required_up_to: "", extension_letter_reference: "", remarks: "" });
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to extend BG");
    } finally {
      setSaving(false);
    }
  };

  const onRelease = async (b: BGDTO) => {
    try {
      await releaseBG(b.id);
      await load();
      toast.success("BG released");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to release BG");
    }
  };

  const onExport = async (format: "csv" | "xlsx" | "pdf") => {
    try {
      const blob = await exportBGs(format, projectFilter !== "all" ? { project_id: projectFilter } : undefined);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url; a.download = `bank-guarantee-register.${format}`; a.click();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Export failed");
    }
  };

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Landmark className="h-6 w-6 text-blue-600" />
          <div>
            <h1 className="text-2xl font-bold">Bank Guarantee Register</h1>
            <p className="text-sm text-muted-foreground">BG lifecycle, extensions and expiry alerts.</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => onExport("csv")}><Download className="mr-2 h-4 w-4" />CSV</Button>
          <Button variant="outline" size="sm" onClick={() => setCsvOpen(true)}><Upload className="mr-2 h-4 w-4" />Upload CSV</Button>
          <Button variant="outline" size="sm" onClick={() => onExport("xlsx")}><Download className="mr-2 h-4 w-4" />Excel</Button>
          <Button variant="outline" size="sm" onClick={() => onExport("pdf")}><Download className="mr-2 h-4 w-4" />PDF</Button>
          <Button onClick={openCreate}><PlusCircle className="mr-2 h-4 w-4" />Add BG</Button>
        </div>
      </div>

      {summary && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
          <Stat label="Total BGs" value={String(summary.total)} />
          <Stat label="Total Amount" value={fmtAmount(summary.total_bg_amount)} cls="text-blue-600" />
          <Stat label="Valid" value={String(summary.valid)} cls="text-green-600" />
          <Stat label="Need Extension" value={String(summary.extension_required)} cls="text-orange-600" />
          <Stat label="Exp ≤45d" value={String(summary.expiring_45)} cls="text-amber-600" />
          <Stat label="Exp ≤30d" value={String(summary.expiring_30)} cls="text-red-600" />
          <Stat label="Expired" value={String(summary.expired)} cls="text-red-700" />
        </div>
      )}

      {alerts.length > 0 && (
        <Card className="border-amber-300 bg-amber-50">
          <CardHeader className="pb-2">
            <CardTitle className="flex items-center gap-2 text-base text-amber-800">
              <AlertTriangle className="h-5 w-5" />
              {alerts.length} bank guarantee{alerts.length > 1 ? "s" : ""} need attention
            </CardTitle>
          </CardHeader>
          <CardContent className="flex flex-wrap gap-2 pt-0">
            {alerts.map((b) => (
              <button
                key={b.id}
                type="button"
                onClick={() => openEdit(b)}
                className="rounded-md border border-amber-300 bg-white px-3 py-1.5 text-left text-sm hover:bg-amber-100"
                title={`Expiry ${fmtDate(b.bg_expiry_date)}`}
              >
                <span className="font-medium">{b.bg_number || bgTypeLabel(b.bg_type)}</span>
                <span className="ml-2 text-amber-700">{bgAlertText(b)}</span>
              </button>
            ))}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Bank Guarantees</CardTitle>
          <div className="flex flex-wrap gap-3 pt-3">
            <Select value={projectFilter} onValueChange={setProjectFilter}>
              <SelectTrigger className="w-56"><SelectValue placeholder="Project" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All projects</SelectItem>
                {projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-48"><SelectValue placeholder="Status" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {BG_STATUSES.map((s) => <SelectItem key={s} value={s}>{bgStatusLabel(s)}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="w-48"><SelectValue placeholder="Type" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All types</SelectItem>
                {BG_TYPES.map((t) => <SelectItem key={t} value={t}>{bgTypeLabel(t)}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Type</TableHead>
                <TableHead>BG No.</TableHead>
                <TableHead>Bank</TableHead>
                <TableHead>Amount</TableHead>
                <TableHead>Required Up To</TableHead>
                <TableHead>Expiry</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Alert</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {items.length === 0 ? (
                <TableRow><TableCell colSpan={9} className="py-8 text-center text-muted-foreground">No bank guarantees yet.</TableCell></TableRow>
              ) : (
                items.map((b) => (
                  <TableRow key={b.id}>
                    <TableCell className="text-xs">{bgTypeLabel(b.bg_type)}</TableCell>
                    <TableCell className="font-mono text-xs">{b.bg_number || "—"}</TableCell>
                    <TableCell className="text-xs">{b.issuing_bank || "—"}</TableCell>
                    <TableCell>
                      {fmtAmount(b.bg_amount, b.currency)}
                      {b.conversion_rate != null && b.conversion_rate !== 1 && b.bg_amount_base != null && (
                        <div className="text-xs text-muted-foreground">≈ {fmtAmount(b.bg_amount_base)} base</div>
                      )}
                    </TableCell>
                    <TableCell>{fmtDate(b.contractual_required_up_to)}</TableCell>
                    <TableCell>{fmtDate(b.bg_expiry_date)}</TableCell>
                    <TableCell><Badge className={bgStatusColor(b.bg_status)}>{bgStatusLabel(b.bg_status)}</Badge></TableCell>
                    <TableCell className="text-xs">
                      {b.extension_required ? (
                        <span className="text-orange-600">{bgAlertText(b)}</span>
                      ) : (
                        bgAlertText(b)
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="ghost" size="icon" className="h-8 w-8" title="Edit" onClick={() => openEdit(b)}><Edit className="h-4 w-4" /></Button>
                        {b.bg_status !== "released" && (
                          <Button variant="ghost" size="icon" className="h-8 w-8" title="Extend" onClick={() => {
                            setExtendBg(b);
                            setExtForm({
                              revised_expiry_date: "", revised_claim_expiry_date: "",
                              revised_required_up_to: b.contractual_required_up_to ? b.contractual_required_up_to.slice(0, 10) : "",
                              extension_letter_reference: "", remarks: "",
                            });
                          }}><CalendarPlus className="h-4 w-4" /></Button>
                        )}
                        {b.bg_status !== "released" && (
                          <Button variant="ghost" size="icon" className="h-8 w-8" title="Release" onClick={() => onRelease(b)}><Unlock className="h-4 w-4" /></Button>
                        )}
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
            <DialogTitle>{editingId ? "Edit Bank Guarantee" : "Add Bank Guarantee"}</DialogTitle>
            <DialogDescription>Extension is flagged when the expiry is before the contractual required-up-to date.</DialogDescription>
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
              <div>
                <Label>BG type</Label>
                <Select value={form.bg_type} onValueChange={(v) => setForm({ ...form, bg_type: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{BG_TYPES.map((t) => <SelectItem key={t} value={t}>{bgTypeLabel(t)}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div><Label>BG number</Label><Input value={form.bg_number} onChange={(e) => setForm({ ...form, bg_number: e.target.value })} /></div>
              <div><Label>Issuing bank</Label><Input value={form.issuing_bank} onChange={(e) => setForm({ ...form, issuing_bank: e.target.value })} /></div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div>
                <Label>Amount</Label>
                <Input type="number" value={form.bg_amount} onChange={(e) => setForm({ ...form, bg_amount: e.target.value })} />
                {form.currency !== formBaseCurrency && form.bg_amount && (
                  <p className="mt-1 text-xs text-muted-foreground">
                    = {fmtAmount(Number(form.bg_amount) * (Number(form.conversion_rate) || 1), formBaseCurrency)} ({formBaseCurrency})
                  </p>
                )}
              </div>
              <div>
                <Label>Currency</Label>
                <select
                  className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                  value={form.currency}
                  onChange={(e) => onBgCurrencyChange(e.target.value)}
                >
                  {Array.from(
                    new Set([formBaseCurrency, ...formCurrencies.map((c) => c.currency), form.currency].filter(Boolean)),
                  ).map((c) => {
                    const r = c === formBaseCurrency ? 1 : formCurrencies.find((x) => x.currency === c)?.conversion_rate;
                    return (
                      <option key={c} value={c}>
                        {c}{c !== formBaseCurrency && r ? ` (×${r})` : ""}
                      </option>
                    );
                  })}
                </select>
              </div>
              <div>
                <Label>Status</Label>
                <Select value={form.bg_status} onValueChange={(v) => setForm({ ...form, bg_status: v })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>{BG_STATUSES.map((s) => <SelectItem key={s} value={s}>{bgStatusLabel(s)}</SelectItem>)}</SelectContent>
                </Select>
              </div>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div><Label>Required up to</Label><Input type="date" value={form.contractual_required_up_to} onChange={(e) => setForm({ ...form, contractual_required_up_to: e.target.value })} /></div>
              <div><Label>Expiry date</Label><Input type="date" value={form.bg_expiry_date} onChange={(e) => setForm({ ...form, bg_expiry_date: e.target.value })} /></div>
              <div><Label>Claim expiry</Label><Input type="date" value={form.claim_expiry_date} onChange={(e) => setForm({ ...form, claim_expiry_date: e.target.value })} /></div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>Cancel</Button>
            <Button onClick={submit} disabled={saving}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlusCircle className="mr-2 h-4 w-4" />}
              {editingId ? "Save changes" : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Extend */}
      <Dialog open={extendBg !== null} onOpenChange={(o) => !o && setExtendBg(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Extend BG {extendBg?.bg_number}</DialogTitle>
            <DialogDescription>Records an immutable extension history revision.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div><Label>Revised expiry date</Label><Input type="date" value={extForm.revised_expiry_date} onChange={(e) => setExtForm({ ...extForm, revised_expiry_date: e.target.value })} /></div>
              <div><Label>Revised claim expiry</Label><Input type="date" value={extForm.revised_claim_expiry_date} onChange={(e) => setExtForm({ ...extForm, revised_claim_expiry_date: e.target.value })} /></div>
            </div>
            <div><Label>Revised required up to</Label><Input type="date" value={extForm.revised_required_up_to} onChange={(e) => setExtForm({ ...extForm, revised_required_up_to: e.target.value })} /></div>
            <div><Label>Extension letter reference</Label><Input value={extForm.extension_letter_reference} onChange={(e) => setExtForm({ ...extForm, extension_letter_reference: e.target.value })} /></div>
            <div><Label>Remarks</Label><Textarea value={extForm.remarks} onChange={(e) => setExtForm({ ...extForm, remarks: e.target.value })} rows={2} /></div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setExtendBg(null)} disabled={saving}>Cancel</Button>
            <Button onClick={onExtend} disabled={saving}>Extend</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <CsvImportDialog
        open={csvOpen}
        onOpenChange={setCsvOpen}
        title="Upload Bank Guarantees CSV"
        description="Preview imported bank guarantees and fix row errors before saving."
        sampleFileName="bank-guarantee-import-template.csv"
        onDownloadTemplate={downloadBGImportTemplate}
        onPreview={(file, scope) => previewBGsCsv(file, scope)}
        onImport={(file, scope) => importBGsCsv(file, scope)}
        onImported={load}
        rowLabel={(row) => String(row.data?.bg_number || row.data?.issuing_bank || `Row ${row.row_number}`)}
      />
    </div>
  );
};

export default BankGuaranteeRegisterPage;
