import React, { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Download, Edit, FileText, Loader2, PlusCircle, Tags, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Table, TableBody, TableCell, TableHead, TableHeader, TableRow,
} from "@/components/ui/table";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import useRBAC from "@/hooks/useRBAC";
import { enhancedApi } from "@/services/enhanced-api";
import { getContractMasterForProject } from "@/services/contract-master-api";
import {
  COMPONENT_FIELDS, COMPONENT_KEYS, COMPONENT_LABELS, ComponentFieldConfig, CurrencyAmount,
  IPCBillDTO, IPCBillSummaryDTO, IPCComponents, IPCStatus, PERSPECTIVE_KEYS, PERSPECTIVE_LABELS,
  PaymentStructure, PerspectiveKey, componentBase, createIPCBill, deleteIPCBill, emptyComponents,
  exportIPCBills, getIPCBills, getIPCSummary, updateIPCBill,
} from "@/services/ipc-bills-api";
import {
  IPCCategory, IPCCategoryKind, createIPCCategory, deleteIPCCategory, getIPCCategories,
  getIPCCategoriesManage, updateIPCCategory,
} from "@/services/ipc-categories-api";

const STATUSES: IPCStatus[] = ["draft", "submitted", "under_verification", "verified", "approved", "partially_paid", "paid", "rejected"];
const PAY_STRUCT: PaymentStructure[] = ["full", "80_20", "20", "partial", "custom"];
const statusColor: Record<string, string> = {
  draft: "bg-gray-100 text-gray-700", submitted: "bg-blue-100 text-blue-800",
  under_verification: "bg-amber-100 text-amber-800", verified: "bg-indigo-100 text-indigo-800",
  approved: "bg-green-100 text-green-800", partially_paid: "bg-cyan-100 text-cyan-800",
  paid: "bg-emerald-100 text-emerald-800", rejected: "bg-red-100 text-red-800",
};
const label = (s?: string | null) => (s || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const fmt = (n?: number | null) => (n == null ? "—" : Number(n).toLocaleString(undefined, { maximumFractionDigits: 2 }));
const fmtDate = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");
const toISO = (d: string) => (d ? new Date(d).toISOString() : undefined);
const dstr = (d?: string | null) => (d ? d.slice(0, 10) : "");

interface HForm {
  project_id: string; ipc_number: string; ipc_period: string; contractor_name: string;
  payment_structure: PaymentStructure; payment_percentage: string; status: IPCStatus;
  base_currency: string; original_contract_value: string; remarks: string; letter_references: string;
  submission_date: string; verification_date: string; approval_date: string; payment_date: string;
}
const EMPTY_H: HForm = {
  project_id: "", ipc_number: "", ipc_period: "", contractor_name: "",
  payment_structure: "full", payment_percentage: "", status: "draft",
  base_currency: "INR", original_contract_value: "", remarks: "", letter_references: "",
  submission_date: "", verification_date: "", approval_date: "", payment_date: "",
};
const EMPTY_PERS = (): Record<PerspectiveKey, IPCComponents> => ({
  contractor_claimed: emptyComponents(), engineer_verified: emptyComponents(),
  employer_approved: emptyComponents(), actually_paid: emptyComponents(),
});

const netOf = (c: IPCComponents): number =>
  componentBase(c.gross) - componentBase(c.deductions) - componentBase(c.recovery_of_advances)
  - componentBase(c.it_tax) - componentBase(c.gst) - componentBase(c.withheld) - componentBase(c.penalties_ld);

const IPCBillRegisterPage: React.FC = () => {
  const { can } = useRBAC();
  const [items, setItems] = useState<IPCBillDTO[]>([]);
  const [summary, setSummary] = useState<IPCBillSummaryDTO | null>(null);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [projectFilter, setProjectFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [payFilter, setPayFilter] = useState("all");
  const [currencyFilter, setCurrencyFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(true);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [header, setHeader] = useState<HForm>({ ...EMPTY_H });
  const [pers, setPers] = useState<Record<PerspectiveKey, IPCComponents>>(EMPTY_PERS());
  const [saving, setSaving] = useState(false);
  const [contractCurrencies, setContractCurrencies] = useState<{ currency: string; conversion_rate: number }[]>([]);
  const [advanceCats, setAdvanceCats] = useState<IPCCategory[]>([]);
  const [deductionCats, setDeductionCats] = useState<IPCCategory[]>([]);
  const [manageOpen, setManageOpen] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const p: Record<string, string> = {};
      if (projectFilter !== "all") p.project_id = projectFilter;
      if (statusFilter !== "all") p.status = statusFilter;
      if (payFilter !== "all") p.payment_status = payFilter;
      if (currencyFilter.trim()) p.currency = currencyFilter.trim().toUpperCase();
      if (dateFrom) p.date_from = new Date(dateFrom).toISOString();
      if (dateTo) p.date_to = new Date(dateTo).toISOString();
      setItems(await getIPCBills(p));
      setSummary(await getIPCSummary(projectFilter !== "all" ? { project_id: projectFilter } : undefined));
    } catch {
      toast.error("Failed to load IPC bills");
    } finally {
      setLoading(false);
    }
  }, [projectFilter, statusFilter, payFilter, currencyFilter, dateFrom, dateTo]);

  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    (async () => {
      try {
        const ps = await enhancedApi.getProjects();
        setProjects((ps || []).map((p: any) => ({ id: String(p._id || p.id || ""), name: p.name || "Project" })));
      } catch { /* optional */ }
    })();
  }, []);

  // Load the contract's currencies + base + value for the form's project.
  useEffect(() => {
    if (!dialogOpen || !header.project_id) { setContractCurrencies([]); return; }
    let active = true;
    (async () => {
      try {
        const cm = await getContractMasterForProject(header.project_id);
        if (!active || !cm) return;
        setContractCurrencies((cm.contract_currencies || []).map((c) => ({ currency: c.currency, conversion_rate: c.conversion_rate })));
        const cmValue = cm.total_contract_value_base ?? cm.original_contract_value;
        setHeader((h) => ({
          ...h,
          base_currency: h.base_currency && h.base_currency !== "INR" ? h.base_currency : (cm.currency || "INR"),
          // Only auto-fill the contract value when the user hasn't entered one.
          original_contract_value:
            h.original_contract_value || cmValue == null ? h.original_contract_value : String(cmValue),
        }));
      } catch { /* optional */ }
    })();
    return () => { active = false; };
  }, [dialogOpen, header.project_id]);

  // Load the advance + deduction catalogs (org-wide + project overrides merged)
  // for the editor dropdowns whenever the dialog opens or the project changes.
  const loadCategories = useCallback(async (projectId?: string) => {
    try {
      const params = projectId ? { project_id: projectId } : undefined;
      const [adv, ded] = await Promise.all([
        getIPCCategories({ ...(params || {}), kind: "advance" }),
        getIPCCategories({ ...(params || {}), kind: "deduction" }),
      ]);
      setAdvanceCats(adv);
      setDeductionCats(ded);
    } catch { /* optional */ }
  }, []);

  useEffect(() => {
    if (!dialogOpen) return;
    void loadCategories(header.project_id || undefined);
  }, [dialogOpen, header.project_id, loadCategories]);

  const catsFor = useCallback(
    (kind?: "advance" | "deduction"): IPCCategory[] =>
      kind === "advance" ? advanceCats : kind === "deduction" ? deductionCats : [],
    [advanceCats, deductionCats]);

  const rateFor = useCallback((cur: string) =>
    cur === header.base_currency ? 1 : contractCurrencies.find((c) => c.currency === cur)?.conversion_rate ?? 1,
    [header.base_currency, contractCurrencies]);

  const currencyOptions = useMemo(
    () => Array.from(new Set([header.base_currency, ...contractCurrencies.map((c) => c.currency)].filter(Boolean))),
    [header.base_currency, contractCurrencies]);

  const openCreate = () => {
    setEditingId(null);
    setHeader({ ...EMPTY_H, project_id: projectFilter !== "all" ? projectFilter : "" });
    setPers(EMPTY_PERS());
    setDialogOpen(true);
  };
  const openEdit = (i: IPCBillDTO) => {
    setEditingId(i.id);
    setHeader({
      project_id: i.project_id || "", ipc_number: i.ipc_number || "", ipc_period: i.ipc_period || "",
      contractor_name: i.contractor_name || "", payment_structure: i.payment_structure || "full",
      payment_percentage: i.payment_percentage != null ? String(i.payment_percentage) : "",
      status: i.status, base_currency: i.base_currency || "INR",
      original_contract_value: i.original_contract_value != null ? String(i.original_contract_value) : "",
      remarks: i.remarks || "", letter_references: (i.letter_references || []).join(", "),
      submission_date: dstr(i.submission_date), verification_date: dstr(i.verification_date),
      approval_date: dstr(i.approval_date), payment_date: dstr(i.payment_date),
    });
    setPers({
      contractor_claimed: { ...emptyComponents(), ...i.contractor_claimed },
      engineer_verified: { ...emptyComponents(), ...i.engineer_verified },
      employer_approved: { ...emptyComponents(), ...i.employer_approved },
      actually_paid: { ...emptyComponents(), ...i.actually_paid },
    });
    setDialogOpen(true);
  };

  // Immutable setter for one component's currency-amount list.
  const setComp = (pk: PerspectiveKey, ck: keyof IPCComponents, rows: CurrencyAmount[]) =>
    setPers((p) => ({ ...p, [pk]: { ...p[pk], [ck]: rows } }));

  const save = async () => {
    if (!header.project_id || !header.ipc_number.trim()) {
      toast.error("Project and IPC number are required");
      return;
    }
    setSaving(true);
    try {
      const payload = {
        project_id: header.project_id,
        ipc_number: header.ipc_number.trim(),
        ipc_period: header.ipc_period || undefined,
        contractor_name: header.contractor_name || undefined,
        payment_structure: header.payment_structure,
        payment_percentage: header.payment_percentage ? Number(header.payment_percentage) : undefined,
        status: header.status,
        base_currency: header.base_currency || "INR",
        original_contract_value: header.original_contract_value ? Number(header.original_contract_value) : undefined,
        remarks: header.remarks || undefined,
        letter_references: header.letter_references.split(",").map((s) => s.trim()).filter(Boolean),
        submission_date: toISO(header.submission_date),
        verification_date: toISO(header.verification_date),
        approval_date: toISO(header.approval_date),
        payment_date: toISO(header.payment_date),
        contractor_claimed: pers.contractor_claimed,
        engineer_verified: pers.engineer_verified,
        employer_approved: pers.employer_approved,
        actually_paid: pers.actually_paid,
      };
      if (editingId) await updateIPCBill(editingId, payload);
      else await createIPCBill(payload);
      toast.success(editingId ? "IPC updated" : "IPC created");
      setDialogOpen(false);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || "Failed to save IPC");
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async (i: IPCBillDTO) => {
    if (!window.confirm(`Delete IPC ${i.ipc_number || ""}?`)) return;
    try {
      await deleteIPCBill(i.id);
      toast.success("IPC deleted");
      setItems((prev) => prev.filter((x) => x.id !== i.id));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to delete IPC");
    }
  };

  const onExport = async (format: "csv" | "xlsx", ipc_id?: string) => {
    try {
      const blob = await exportIPCBills(format, {
        ...(projectFilter !== "all" ? { project_id: projectFilter } : {}),
        ...(ipc_id ? { ipc_id } : {}),
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${ipc_id ? "ipc" : "ipc-register"}.${format === "xlsx" ? "xlsx" : "csv"}`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Export failed");
    }
  };

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <FileText className="h-6 w-6 text-blue-600" />
          <div>
            <h1 className="text-2xl font-bold">IPC / Bill Register</h1>
            <p className="text-sm text-muted-foreground">Interim Payment Certificates: claimed vs verified vs approved vs paid.</p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          {can("dms.ipc.export") && (
            <>
              <Button variant="outline" size="sm" onClick={() => onExport("csv")}><Download className="mr-2 h-4 w-4" />CSV</Button>
              <Button variant="outline" size="sm" onClick={() => onExport("xlsx")}><Download className="mr-2 h-4 w-4" />Excel</Button>
            </>
          )}
          {can("dms.ipc.edit") && (
            <Button variant="outline" size="sm" onClick={() => setManageOpen(true)}><Tags className="mr-2 h-4 w-4" />Manage types</Button>
          )}
          {can("dms.ipc.create") && <Button onClick={openCreate}><PlusCircle className="mr-2 h-4 w-4" />Add IPC</Button>}
        </div>
      </div>

      {summary && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4 lg:grid-cols-7">
          <Stat label={`Total IPCs`} value={String(summary.total_ipcs)} />
          <Stat label={`Claimed (${summary.base_currency})`} value={fmt(summary.total_claimed_base)} cls="text-blue-600" />
          <Stat label="Approved" value={fmt(summary.total_approved_base)} cls="text-green-600" />
          <Stat label="Net Payable" value={fmt(summary.total_net_payable_base)} cls="text-indigo-600" />
          <Stat label="Paid" value={fmt(summary.total_paid_base)} cls="text-emerald-600" />
          <Stat label="Balance" value={fmt(summary.total_balance_payable_base)} cls="text-amber-600" />
          <Stat label="% Billed" value={`${summary.percent_of_contract_billed}%`} cls="text-purple-600" />
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle>IPC Register</CardTitle>
          <div className="flex flex-wrap gap-3 pt-3">
            <Select value={projectFilter} onValueChange={setProjectFilter}>
              <SelectTrigger className="w-52"><SelectValue placeholder="Project" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All projects</SelectItem>
                {projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-44"><SelectValue placeholder="Status" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {STATUSES.map((s) => <SelectItem key={s} value={s}>{label(s)}</SelectItem>)}
              </SelectContent>
            </Select>
            <Select value={payFilter} onValueChange={setPayFilter}>
              <SelectTrigger className="w-40"><SelectValue placeholder="Payment" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">Any payment</SelectItem>
                <SelectItem value="unpaid">Unpaid</SelectItem>
                <SelectItem value="partial">Partial</SelectItem>
                <SelectItem value="paid">Paid</SelectItem>
              </SelectContent>
            </Select>
            <Input className="w-28" placeholder="Currency" value={currencyFilter} onChange={(e) => setCurrencyFilter(e.target.value)} />
            <Input className="w-40" type="date" value={dateFrom} onChange={(e) => setDateFrom(e.target.value)} />
            <Input className="w-40" type="date" value={dateTo} onChange={(e) => setDateTo(e.target.value)} />
          </div>
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground"><Loader2 className="mr-2 h-5 w-5 animate-spin" />Loading…</div>
          ) : items.length === 0 ? (
            <div className="py-12 text-center text-muted-foreground">No IPC bills found.</div>
          ) : (
            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>IPC No</TableHead><TableHead>Period</TableHead><TableHead>Contractor</TableHead>
                    <TableHead>Status</TableHead><TableHead className="text-right">Claimed</TableHead>
                    <TableHead className="text-right">Approved</TableHead><TableHead className="text-right">Net Payable</TableHead>
                    <TableHead className="text-right">Paid</TableHead><TableHead className="text-right">Balance</TableHead>
                    <TableHead className="text-right">% Billed</TableHead><TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {items.map((i) => (
                    <TableRow key={i.id}>
                      <TableCell className="font-medium">{i.ipc_number || "—"}</TableCell>
                      <TableCell>{i.ipc_period || "—"}</TableCell>
                      <TableCell>{i.contractor_name || "—"}</TableCell>
                      <TableCell><Badge variant="secondary" className={statusColor[i.status]}>{label(i.status)}</Badge></TableCell>
                      <TableCell className="text-right">{fmt(i.claimed_total_base)}</TableCell>
                      <TableCell className="text-right">{fmt(i.approved_total_base)}</TableCell>
                      <TableCell className="text-right">{fmt(i.net_payable_base)}</TableCell>
                      <TableCell className="text-right">{fmt(i.paid_base)}</TableCell>
                      <TableCell className="text-right">{fmt(i.balance_payable_base)}</TableCell>
                      <TableCell className="text-right">{i.percent_billed != null ? `${i.percent_billed}%` : "—"}</TableCell>
                      <TableCell className="text-right whitespace-nowrap">
                        {can("dms.ipc.export") && (
                          <Button variant="ghost" size="icon" className="h-8 w-8" title="Export this IPC" onClick={() => onExport("xlsx", i.id)}><Download className="h-4 w-4" /></Button>
                        )}
                        {can("dms.ipc.edit") && (
                          <Button variant="ghost" size="icon" className="h-8 w-8" title="Edit" onClick={() => openEdit(i)}><Edit className="h-4 w-4" /></Button>
                        )}
                        {can("dms.ipc.delete") && (
                          <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" title="Delete" onClick={() => onDelete(i)}><Trash2 className="h-4 w-4" /></Button>
                        )}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[820px]">
          <DialogHeader>
            <DialogTitle>{editingId ? "Edit IPC" : "Add IPC"}</DialogTitle>
            <DialogDescription>Amounts can be split across the contract currencies; rates are fixed from Contract Master.</DialogDescription>
          </DialogHeader>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
            <Field label="Project">
              <Select value={header.project_id} onValueChange={(v) => setHeader((h) => ({ ...h, project_id: v }))}>
                <SelectTrigger><SelectValue placeholder="Project" /></SelectTrigger>
                <SelectContent>{projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}</SelectContent>
              </Select>
            </Field>
            <Field label="IPC number"><Input value={header.ipc_number} onChange={(e) => setHeader((h) => ({ ...h, ipc_number: e.target.value }))} placeholder="IPC-001" /></Field>
            <Field label="Period"><Input value={header.ipc_period} onChange={(e) => setHeader((h) => ({ ...h, ipc_period: e.target.value }))} placeholder="Jan 2026" /></Field>
            <Field label="Contractor"><Input value={header.contractor_name} onChange={(e) => setHeader((h) => ({ ...h, contractor_name: e.target.value }))} /></Field>
            <Field label="Base currency"><Input value={header.base_currency} onChange={(e) => setHeader((h) => ({ ...h, base_currency: e.target.value.toUpperCase() }))} /></Field>
            <Field label="Contract value (base)"><Input type="number" value={header.original_contract_value} onChange={(e) => setHeader((h) => ({ ...h, original_contract_value: e.target.value }))} /></Field>
            <Field label="Payment structure">
              <Select value={header.payment_structure} onValueChange={(v) => setHeader((h) => ({ ...h, payment_structure: v as PaymentStructure }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{PAY_STRUCT.map((s) => <SelectItem key={s} value={s}>{label(s)}</SelectItem>)}</SelectContent>
              </Select>
            </Field>
            <Field label="Payment %"><Input type="number" value={header.payment_percentage} onChange={(e) => setHeader((h) => ({ ...h, payment_percentage: e.target.value }))} placeholder="80" /></Field>
            <Field label="Status">
              <Select value={header.status} onValueChange={(v) => setHeader((h) => ({ ...h, status: v as IPCStatus }))}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>{STATUSES.map((s) => <SelectItem key={s} value={s}>{label(s)}</SelectItem>)}</SelectContent>
              </Select>
            </Field>
            <Field label="Submission date"><Input type="date" value={header.submission_date} onChange={(e) => setHeader((h) => ({ ...h, submission_date: e.target.value }))} /></Field>
            <Field label="Verification date"><Input type="date" value={header.verification_date} onChange={(e) => setHeader((h) => ({ ...h, verification_date: e.target.value }))} /></Field>
            <Field label="Approval date"><Input type="date" value={header.approval_date} onChange={(e) => setHeader((h) => ({ ...h, approval_date: e.target.value }))} /></Field>
            <Field label="Payment date"><Input type="date" value={header.payment_date} onChange={(e) => setHeader((h) => ({ ...h, payment_date: e.target.value }))} /></Field>
            <Field label="Letter references"><Input value={header.letter_references} onChange={(e) => setHeader((h) => ({ ...h, letter_references: e.target.value }))} placeholder="L-1, L-2" /></Field>
          </div>
          <Field label="Remarks"><Textarea rows={2} value={header.remarks} onChange={(e) => setHeader((h) => ({ ...h, remarks: e.target.value }))} /></Field>

          <Tabs defaultValue={PERSPECTIVE_KEYS[0]} className="pt-1">
            <TabsList className="flex h-auto flex-wrap justify-start">
              {PERSPECTIVE_KEYS.map((pk) => <TabsTrigger key={pk} value={pk}>{PERSPECTIVE_LABELS[pk]}</TabsTrigger>)}
            </TabsList>
            {PERSPECTIVE_KEYS.map((pk) => (
              <TabsContent key={pk} value={pk} className="space-y-3">
                {COMPONENT_KEYS.map((ck) => (
                  <ComponentEditor
                    key={ck}
                    title={COMPONENT_LABELS[ck]}
                    rows={pers[pk][ck]}
                    baseCurrency={header.base_currency}
                    currencyOptions={currencyOptions}
                    rateFor={rateFor}
                    fieldConfig={COMPONENT_FIELDS[ck]}
                    categories={catsFor(COMPONENT_FIELDS[ck].categoryKind)}
                    onChange={(rows) => setComp(pk, ck, rows)}
                  />
                ))}
                <div className="rounded-md bg-muted/40 px-3 py-2 text-sm font-medium">
                  Net payable ({header.base_currency}): {fmt(netOf(pers[pk]))}
                </div>
              </TabsContent>
            ))}
          </Tabs>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button onClick={save} disabled={saving}>{saving ? "Saving…" : editingId ? "Save changes" : "Create"}</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ManageCategoriesDialog
        open={manageOpen}
        onOpenChange={setManageOpen}
        projects={projects}
        defaultProjectId={projectFilter !== "all" ? projectFilter : ""}
        canEdit={can("dms.ipc.edit")}
        onChanged={() => { if (dialogOpen) void loadCategories(header.project_id || undefined); }}
      />
    </div>
  );
};

const Stat: React.FC<{ label: string; value: string; cls?: string }> = ({ label, value, cls }) => (
  <Card><CardHeader className="pb-2"><CardDescription className="text-xs">{label}</CardDescription><CardTitle className={`text-xl ${cls || ""}`}>{value}</CardTitle></CardHeader></Card>
);

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="grid gap-1.5"><Label className="text-xs text-muted-foreground">{label}</Label>{children}</div>
);

// One component as a list of rows. Each row is {currency, amount} plus, where
// the component config asks for it, a typed category (advance/deduction master)
// and/or a free-text description. Rate auto-filled from the contract currencies.
const ComponentEditor: React.FC<{
  title: string; rows: CurrencyAmount[]; baseCurrency: string;
  currencyOptions: string[]; rateFor: (c: string) => number;
  fieldConfig: ComponentFieldConfig; categories: IPCCategory[];
  onChange: (rows: CurrencyAmount[]) => void;
}> = ({ title, rows, baseCurrency, currencyOptions, rateFor, fieldConfig, categories, onChange }) => {
  const add = () => onChange([...(rows || []), { currency: baseCurrency, conversion_rate: 1, amount: 0 }]);
  const set = (i: number, patch: Partial<CurrencyAmount>) =>
    onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const base = componentBase(rows);
  const hasCategory = !!fieldConfig.categoryKind;
  const cols = hasCategory ? "grid-cols-[1.2fr_1fr_1fr_auto]" : "grid-cols-[1fr_1fr_auto]";
  return (
    <div className="rounded-md border p-2">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium">{title}</span>
        <div className="flex items-center gap-2">
          {rows.length > 0 && <span className="text-xs text-muted-foreground">= {fmt(base)} {baseCurrency}</span>}
          <Button type="button" variant="ghost" size="sm" className="h-7" onClick={add}><PlusCircle className="mr-1 h-3.5 w-3.5" />line</Button>
        </div>
      </div>
      {rows.map((r, i) => (
        <div key={i} className="mt-1 space-y-1">
          <div className={`grid ${cols} gap-2`}>
            {hasCategory && (
              <select
                className="flex h-9 rounded-md border border-input bg-background px-2 text-sm"
                value={r.category || ""}
                onChange={(e) => set(i, { category: e.target.value || null })}
              >
                <option value="">— type —</option>
                {categories.map((c) => <option key={c.id} value={c.code}>{c.name}</option>)}
                {r.category && !categories.some((c) => c.code === r.category) && (
                  <option value={r.category}>{r.category}</option>
                )}
              </select>
            )}
            <select
              className="flex h-9 rounded-md border border-input bg-background px-2 text-sm"
              value={r.currency}
              onChange={(e) => { const cur = e.target.value; set(i, { currency: cur, conversion_rate: rateFor(cur) }); }}
            >
              {Array.from(new Set([...currencyOptions, r.currency].filter(Boolean))).map((c) => (
                <option key={c} value={c}>{c}{c !== baseCurrency ? ` (×${rateFor(c)})` : ""}</option>
              ))}
            </select>
            <Input type="number" className="h-9" placeholder="0" value={r.amount}
              onChange={(e) => set(i, { amount: Number(e.target.value) })} />
            <Button type="button" variant="ghost" size="icon" className="h-9 w-9 text-destructive" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
              <Trash2 className="h-4 w-4" />
            </Button>
          </div>
          {fieldConfig.description && (
            <Input className="h-8 text-sm" placeholder="Description / reason"
              value={r.description || ""} onChange={(e) => set(i, { description: e.target.value || null })} />
          )}
        </div>
      ))}
    </div>
  );
};

// Master management: create / rename / activate / delete advance + deduction
// types. Scope is org-wide (project = "") or a project override/addition.
interface CatForm { id: string; kind: IPCCategoryKind; name: string; description: string; scope: string; }
const EMPTY_CAT: CatForm = { id: "", kind: "deduction", name: "", description: "", scope: "" };

const ManageCategoriesDialog: React.FC<{
  open: boolean; onOpenChange: (v: boolean) => void;
  projects: { id: string; name: string }[]; defaultProjectId: string;
  canEdit: boolean; onChanged: () => void;
}> = ({ open, onOpenChange, projects, defaultProjectId, canEdit, onChanged }) => {
  const [scope, setScope] = useState<string>(defaultProjectId);
  const [items, setItems] = useState<IPCCategory[]>([]);
  const [loading, setLoading] = useState(false);
  const [form, setForm] = useState<CatForm>({ ...EMPTY_CAT });
  const [busy, setBusy] = useState(false);
  const projectName = (id?: string | null) => projects.find((p) => p.id === id)?.name || "Project";

  const reload = useCallback(async (proj: string) => {
    setLoading(true);
    try {
      setItems(await getIPCCategoriesManage(proj ? { project_id: proj } : undefined));
    } catch { toast.error("Failed to load types"); } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    if (!open) return;
    setScope(defaultProjectId);
    setForm({ ...EMPTY_CAT, scope: defaultProjectId });
  }, [open, defaultProjectId]);
  useEffect(() => { if (open) void reload(scope); }, [open, scope, reload]);

  const submit = async () => {
    if (!form.name.trim()) { toast.error("Name is required"); return; }
    setBusy(true);
    try {
      if (form.id) {
        await updateIPCCategory(form.id, { name: form.name.trim(), description: form.description || undefined });
        toast.success("Type updated");
      } else {
        await createIPCCategory({
          kind: form.kind,
          code: form.name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, ""),
          name: form.name.trim(),
          description: form.description || undefined,
          project_id: form.scope || null,
        });
        toast.success("Type added");
      }
      setForm({ ...EMPTY_CAT, kind: form.kind, scope: form.scope });
      await reload(scope);
      onChanged();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to save type");
    } finally { setBusy(false); }
  };

  const toggleActive = async (c: IPCCategory) => {
    try { await updateIPCCategory(c.id, { active: !c.active }); await reload(scope); onChanged(); }
    catch { toast.error("Failed to update"); }
  };
  const remove = async (c: IPCCategory) => {
    if (!window.confirm(`Delete "${c.name}"?`)) return;
    try { await deleteIPCCategory(c.id); await reload(scope); onChanged(); }
    catch (e: any) { toast.error(e?.response?.data?.detail || "Failed to delete"); }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[760px]">
        <DialogHeader>
          <DialogTitle>Manage advance &amp; deduction types</DialogTitle>
          <DialogDescription>
            Org-wide types apply to every project; a project can add or override types. Recoveries pick an
            advance type; deductions pick a deduction type.
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2">
          <Label className="text-xs text-muted-foreground">Scope</Label>
          <Select value={scope || "org"} onValueChange={(v) => setScope(v === "org" ? "" : v)}>
            <SelectTrigger className="w-60"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="org">Org-wide</SelectItem>
              {projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name} (project)</SelectItem>)}
            </SelectContent>
          </Select>
        </div>

        {canEdit && (
          <div className="grid grid-cols-2 gap-2 rounded-md border p-3 md:grid-cols-[140px_1fr_1fr_140px_auto]">
            <div className="grid gap-1.5">
              <Label className="text-xs text-muted-foreground">Kind</Label>
              <Select value={form.kind} onValueChange={(v) => setForm((f) => ({ ...f, kind: v as IPCCategoryKind }))} disabled={!!form.id}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="advance">Advance</SelectItem>
                  <SelectItem value="deduction">Deduction</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs text-muted-foreground">Name</Label>
              <Input value={form.name} onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))} placeholder="e.g. Retention" />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs text-muted-foreground">Description</Label>
              <Input value={form.description} onChange={(e) => setForm((f) => ({ ...f, description: e.target.value }))} placeholder="optional" />
            </div>
            <div className="grid gap-1.5">
              <Label className="text-xs text-muted-foreground">Add to</Label>
              <Select value={form.scope || "org"} onValueChange={(v) => setForm((f) => ({ ...f, scope: v === "org" ? "" : v }))} disabled={!!form.id}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="org">Org-wide</SelectItem>
                  {projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="flex items-end gap-1">
              <Button size="sm" onClick={submit} disabled={busy}>{form.id ? "Save" : "Add"}</Button>
              {form.id && <Button size="sm" variant="ghost" onClick={() => setForm({ ...EMPTY_CAT, kind: form.kind, scope: form.scope })}>Cancel</Button>}
            </div>
          </div>
        )}

        {loading ? (
          <div className="flex items-center justify-center py-8 text-muted-foreground"><Loader2 className="mr-2 h-5 w-5 animate-spin" />Loading…</div>
        ) : (
          <div className="overflow-x-auto">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Type</TableHead><TableHead>Kind</TableHead><TableHead>Scope</TableHead>
                  <TableHead>Status</TableHead><TableHead className="text-right">Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {items.length === 0 ? (
                  <TableRow><TableCell colSpan={5} className="py-6 text-center text-muted-foreground">No types defined.</TableCell></TableRow>
                ) : items.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell>
                      <div className="font-medium">{c.name}</div>
                      <div className="text-xs text-muted-foreground">{c.code}{c.description ? ` · ${c.description}` : ""}</div>
                    </TableCell>
                    <TableCell><Badge variant="secondary">{label(c.kind)}</Badge></TableCell>
                    <TableCell>{c.project_id ? projectName(c.project_id) : "Org-wide"}{c.is_default && <span className="ml-1 text-xs text-muted-foreground">(default)</span>}</TableCell>
                    <TableCell><Badge variant="secondary" className={c.active ? "bg-green-100 text-green-800" : "bg-gray-100 text-gray-600"}>{c.active ? "Active" : "Inactive"}</Badge></TableCell>
                    <TableCell className="text-right whitespace-nowrap">
                      {canEdit && (
                        <>
                          <Button variant="ghost" size="icon" className="h-8 w-8" title="Edit"
                            onClick={() => setForm({ id: c.id, kind: c.kind, name: c.name, description: c.description || "", scope: c.project_id || "" })}><Edit className="h-4 w-4" /></Button>
                          <Button variant="ghost" size="sm" className="h-8" title="Toggle active" onClick={() => toggleActive(c)}>{c.active ? "Disable" : "Enable"}</Button>
                          <Button variant="ghost" size="icon" className="h-8 w-8 text-destructive" title="Delete" onClick={() => remove(c)}><Trash2 className="h-4 w-4" /></Button>
                        </>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Close</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
};

export default IPCBillRegisterPage;
