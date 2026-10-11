import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Download, Edit, FileText, Loader2, PlusCircle, Search, Tags, Trash2, X } from "lucide-react";
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
import { useRegisterProjectScope } from "@/hooks/useRegisterProjectScope";
import { scopeRefusalMessage } from "@/services/active-scope";
import { enhancedApi } from "@/services/enhanced-api";
import { getContractMasterForProject } from "@/services/contract-master-api";
import {
  COMPONENT_FIELDS, ComponentFieldConfig, DEDUCTION_KEYS, DEDUCTION_LABELS, IPCBillDTO,
  IPCBillSummaryDTO, IPCDeductionLine, IPCDeductions, IPCLineItem, IPCPaymentRecord, IPCRevision,
  IPCStatus, PaymentStructure, componentBase, createIPCBill, deductionsColTotal, deleteIPCBill,
  emptyDeductions, exportIPCBills, getIPCBill, getIPCBills, getIPCSummary, lineTotal, updateIPCBill,
} from "@/services/ipc-bills-api";
import {
  IPCCategory, IPCCategoryKind, createIPCCategory, deleteIPCCategory, getIPCCategories,
  getIPCCategoriesManage, updateIPCCategory,
} from "@/services/ipc-categories-api";
import EntityDocumentLinks from "@/components/document-links/EntityDocumentLinks";
import { IPC_DOCUMENT_RELATIONSHIP_ROLES } from "@/services/document-relationships-api";

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
  project_id: string; ipc_number: string; ipc_date: string; period_from: string; period_to: string;
  contractor_name: string; approver: string; payment_structure: PaymentStructure; payment_percentage: string;
  status: IPCStatus; base_currency: string; original_contract_value: string; remarks: string;
  letter_references: string;
}
const EMPTY_H: HForm = {
  project_id: "", ipc_number: "", ipc_date: "", period_from: "", period_to: "",
  contractor_name: "", approver: "", payment_structure: "full", payment_percentage: "",
  status: "draft", base_currency: "INR", original_contract_value: "", remarks: "",
  letter_references: "",
};

const IPCBillRegisterPage: React.FC = () => {
  const { can } = useRBAC();
  const canEditIPC = can("dms.ipc.edit");
  const [searchParams] = useSearchParams();
  const deepLinkId = searchParams.get("ipc_id");
  const openedDeepLink = useRef<string | null>(null);
  const [items, setItems] = useState<IPCBillDTO[]>([]);
  const [summary, setSummary] = useState<IPCBillSummaryDTO | null>(null);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  // CL-4A: the navbar project pins this filter (useRegisterProjectScope).
  const { projectFilter, setProjectFilter, projectLocked, tenantLoading } = useRegisterProjectScope();
  const [statusFilter, setStatusFilter] = useState("all");
  const [payFilter, setPayFilter] = useState("all");
  const [currencyFilter, setCurrencyFilter] = useState("");
  const [dateFrom, setDateFrom] = useState("");
  const [dateTo, setDateTo] = useState("");
  const [loading, setLoading] = useState(true);

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingOrganizationId, setEditingOrganizationId] = useState<string | null>(null);
  const [editingProjectId, setEditingProjectId] = useState<string | null>(null);
  const [header, setHeader] = useState<HForm>({ ...EMPTY_H });
  const [lineItems, setLineItems] = useState<IPCLineItem[]>([]);
  const [deductions, setDeductions] = useState<IPCDeductions>(emptyDeductions());
  const [payments, setPayments] = useState<IPCPaymentRecord[]>([]);
  const [revisions, setRevisions] = useState<IPCRevision[]>([]);
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
    } catch (error) {
      toast.error(scopeRefusalMessage(error, "IPC") || "Failed to load IPC bills");
    } finally {
      setLoading(false);
    }
  }, [projectFilter, statusFilter, payFilter, currencyFilter, dateFrom, dateTo]);

  useEffect(() => { if (!tenantLoading) void load(); }, [load, tenantLoading]);
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
          original_contract_value:
            h.original_contract_value || cmValue == null ? h.original_contract_value : String(cmValue),
        }));
      } catch { /* optional */ }
    })();
    return () => { active = false; };
  }, [dialogOpen, header.project_id]);

  // Load the advance + deduction catalogs (org-wide + project overrides) for the editor.
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

  // Derived editor totals (base currency).
  const totals = useMemo(() => {
    const claimed = lineTotal(lineItems, "claimed");
    const verified = lineTotal(lineItems, "verified");
    const approved = lineTotal(lineItems, "approved");
    const approvedDed = deductionsColTotal(deductions, "approved");
    const net = approved - approvedDed;
    const paid = componentBase(payments);
    return { claimed, verified, approved, approvedDed, net, paid, balance: net - paid };
  }, [lineItems, deductions, payments]);

  const openCreate = () => {
    setEditingId(null);
    setEditingOrganizationId(null);
    setEditingProjectId(null);
    setHeader({ ...EMPTY_H, project_id: projectFilter !== "all" ? projectFilter : "" });
    setLineItems([]);
    setDeductions(emptyDeductions());
    setPayments([]);
    setRevisions([]);
    setDialogOpen(true);
  };
  const openEdit = (i: IPCBillDTO) => {
    setEditingId(i.id);
    setEditingOrganizationId(i.organization_id || null);
    setEditingProjectId(i.project_id || null);
    setHeader({
      project_id: i.project_id || "", ipc_number: i.ipc_number || "", ipc_date: dstr(i.ipc_date),
      period_from: dstr(i.period_from), period_to: dstr(i.period_to), contractor_name: i.contractor_name || "",
      approver: i.approver || "", payment_structure: i.payment_structure || "full",
      payment_percentage: i.payment_percentage != null ? String(i.payment_percentage) : "",
      status: i.status, base_currency: i.base_currency || "INR",
      original_contract_value: i.original_contract_value != null ? String(i.original_contract_value) : "",
      remarks: i.remarks || "", letter_references: (i.letter_references || []).join(", "),
    });
    setLineItems((i.line_items || []).map((li) => ({ ...li })));
    setDeductions({ ...emptyDeductions(), ...i.deductions });
    setPayments((i.payments || []).map((p) => ({ ...p })));
    setRevisions(i.revisions || []);
    setDialogOpen(true);
  };

  useEffect(() => {
    if (!deepLinkId || loading || openedDeepLink.current === deepLinkId) return;
    openedDeepLink.current = deepLinkId;
    const local = items.find((item) => item.id === deepLinkId);
    if (local) {
      openEdit(local);
      return;
    }
    void getIPCBill(deepLinkId)
      .then(openEdit)
      .catch((error) => toast.error(scopeRefusalMessage(error, "IPC") || "Linked IPC could not be opened"));
  }, [deepLinkId, items, loading]);

  const setDedComp = (ck: keyof IPCDeductions, rows: IPCDeductionLine[]) =>
    setDeductions((d) => ({ ...d, [ck]: rows }));

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
        ipc_date: toISO(header.ipc_date),
        period_from: toISO(header.period_from),
        period_to: toISO(header.period_to),
        contractor_name: header.contractor_name || undefined,
        approver: header.approver || undefined,
        payment_structure: header.payment_structure,
        payment_percentage: header.payment_percentage ? Number(header.payment_percentage) : undefined,
        status: header.status,
        base_currency: header.base_currency || "INR",
        original_contract_value: header.original_contract_value ? Number(header.original_contract_value) : undefined,
        remarks: header.remarks || undefined,
        letter_references: header.letter_references.split(",").map((s) => s.trim()).filter(Boolean),
        line_items: lineItems,
        deductions,
        payments,
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
            <Select value={projectFilter} onValueChange={setProjectFilter} disabled={projectLocked}>
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
                    <TableHead>IPC No</TableHead><TableHead>Date</TableHead><TableHead>Contractor</TableHead>
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
                      <TableCell>{fmtDate(i.ipc_date) }</TableCell>
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
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-[880px]">
          <DialogHeader>
            <DialogTitle>{editingId ? (canEditIPC ? "Edit IPC" : "View IPC") : "Add IPC"}{header.ipc_number ? ` — ${header.ipc_number}` : ""}</DialogTitle>
            <DialogDescription>Interim Payment Certificate — claimed, verified, approved &amp; paid amounts.</DialogDescription>
          </DialogHeader>

          <Tabs defaultValue="header" className="pt-1">
            <TabsList className="flex h-auto flex-wrap justify-start">
              <TabsTrigger value="header">Header</TabsTrigger>
              <TabsTrigger value="line">Line items</TabsTrigger>
              {DEDUCTION_KEYS.map((ck) => <TabsTrigger key={ck} value={ck}>{DEDUCTION_LABELS[ck]}</TabsTrigger>)}
              <TabsTrigger value="pay">Payments</TabsTrigger>
              <TabsTrigger value="docs">Docs &amp; history</TabsTrigger>
            </TabsList>

            <fieldset disabled={Boolean(editingId) && !canEditIPC} className="contents">
            <TabsContent value="header" className="space-y-3">
              <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                <Field label="IPC number"><Input value={header.ipc_number} onChange={(e) => setHeader((h) => ({ ...h, ipc_number: e.target.value }))} placeholder="IPC-001" /></Field>
                <Field label="IPC date"><Input type="date" value={header.ipc_date} onChange={(e) => setHeader((h) => ({ ...h, ipc_date: e.target.value }))} /></Field>
                <Field label="Status">
                  <Select value={header.status} onValueChange={(v) => setHeader((h) => ({ ...h, status: v as IPCStatus }))}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>{STATUSES.map((s) => <SelectItem key={s} value={s}>{label(s)}</SelectItem>)}</SelectContent>
                  </Select>
                </Field>
                <Field label="Project">
                  <Select value={header.project_id} onValueChange={(v) => setHeader((h) => ({ ...h, project_id: v }))}>
                    <SelectTrigger><SelectValue placeholder="Project" /></SelectTrigger>
                    <SelectContent>{projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}</SelectContent>
                  </Select>
                </Field>
                <Field label="Contractor"><Input value={header.contractor_name} onChange={(e) => setHeader((h) => ({ ...h, contractor_name: e.target.value }))} /></Field>
                <Field label="Primary currency"><Input value={header.base_currency} onChange={(e) => setHeader((h) => ({ ...h, base_currency: e.target.value.toUpperCase() }))} /></Field>
                <Field label="Period from"><Input type="date" value={header.period_from} onChange={(e) => setHeader((h) => ({ ...h, period_from: e.target.value }))} /></Field>
                <Field label="Period to"><Input type="date" value={header.period_to} onChange={(e) => setHeader((h) => ({ ...h, period_to: e.target.value }))} /></Field>
                <Field label="Letter references"><Input value={header.letter_references} onChange={(e) => setHeader((h) => ({ ...h, letter_references: e.target.value }))} placeholder="L-1, L-2" /></Field>
                <Field label="Approver"><Input value={header.approver} onChange={(e) => setHeader((h) => ({ ...h, approver: e.target.value }))} placeholder="Employer's Representative" /></Field>
                <Field label="Payment structure">
                  <Select value={header.payment_structure} onValueChange={(v) => setHeader((h) => ({ ...h, payment_structure: v as PaymentStructure }))}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>{PAY_STRUCT.map((s) => <SelectItem key={s} value={s}>{label(s)}</SelectItem>)}</SelectContent>
                  </Select>
                </Field>
                <Field label="Payment %"><Input type="number" value={header.payment_percentage} onChange={(e) => setHeader((h) => ({ ...h, payment_percentage: e.target.value }))} placeholder="80" /></Field>
                <Field label="Contract value (base)"><Input type="number" value={header.original_contract_value} onChange={(e) => setHeader((h) => ({ ...h, original_contract_value: e.target.value }))} /></Field>
              </div>
              <Field label="Remarks"><Textarea rows={2} value={header.remarks} onChange={(e) => setHeader((h) => ({ ...h, remarks: e.target.value }))} /></Field>
            </TabsContent>

            <TabsContent value="line" className="space-y-3">
              <LineItemsEditor
                rows={lineItems} baseCurrency={header.base_currency} currencyOptions={currencyOptions}
                rateFor={rateFor} onChange={setLineItems}
              />
              <Card>
                <CardHeader className="flex flex-row items-center justify-between pb-2">
                  <CardTitle className="text-sm">Auto-calculated totals</CardTitle>
                  <Badge variant="secondary">Primary: {header.base_currency}</Badge>
                </CardHeader>
                <CardContent className="grid grid-cols-2 gap-3 md:grid-cols-4">
                  <Mini label="Claimed" value={fmt(totals.claimed)} />
                  <Mini label="Verified" value={fmt(totals.verified)} />
                  <Mini label="Approved" value={fmt(totals.approved)} />
                  <Mini label="Total deductions" value={fmt(totals.approvedDed)} />
                  <Mini label="Net payable" value={fmt(totals.net)} />
                  <Mini label="Paid" value={fmt(totals.paid)} />
                  <Mini label="Balance" value={fmt(totals.balance)} />
                </CardContent>
              </Card>
            </TabsContent>

            {DEDUCTION_KEYS.map((ck) => (
              <TabsContent key={ck} value={ck} className="space-y-3">
                <DeductionLinesEditor
                  title={DEDUCTION_LABELS[ck]}
                  rows={deductions[ck]}
                  baseCurrency={header.base_currency}
                  currencyOptions={currencyOptions}
                  rateFor={rateFor}
                  fieldConfig={COMPONENT_FIELDS[ck]}
                  categories={catsFor(COMPONENT_FIELDS[ck].categoryKind)}
                  onChange={(rows) => setDedComp(ck, rows)}
                />
              </TabsContent>
            ))}

            <TabsContent value="pay" className="space-y-3">
              <Card>
                <CardContent className="grid grid-cols-3 gap-3 pt-4">
                  <Mini label="Net payable" value={fmt(totals.net)} />
                  <Mini label={`Paid (${header.base_currency})`} value={fmt(totals.paid)} />
                  <Mini label="Balance payable" value={fmt(totals.balance)} />
                </CardContent>
              </Card>
              <PaymentsEditor
                rows={payments} baseCurrency={header.base_currency} currencyOptions={currencyOptions}
                rateFor={rateFor} onChange={setPayments}
              />
            </TabsContent>

            <TabsContent value="docs" className="space-y-3">
              {editingId ? (
                <EntityDocumentLinks
                  targetType="ipc_bill"
                  targetId={editingId}
                  organizationId={editingOrganizationId}
                  projectId={editingProjectId}
                  roles={IPC_DOCUMENT_RELATIONSHIP_ROLES}
                  defaultRole="supporting_document"
                  canManage={can("dms.ipc.edit")}
                />
              ) : (
                <p className="text-sm text-muted-foreground">
                  Create the IPC before linking Documents.
                </p>
              )}
              <Card>
                <CardHeader className="pb-2"><CardTitle className="text-sm">Revision history</CardTitle></CardHeader>
                <CardContent>
                  {revisions.length === 0 ? (
                    <p className="text-sm text-muted-foreground">No revisions yet.</p>
                  ) : (
                    <div className="space-y-2">
                      {[...revisions].reverse().map((r, idx) => (
                        <div key={idx} className="flex items-start gap-3 border-b pb-2 last:border-0">
                          <Badge variant="secondary">Rev {r.revision_number}</Badge>
                          <div className="text-sm">
                            <div>{label(r.status) || "—"}{r.changed_by ? ` · ${r.changed_by}` : ""}</div>
                            <div className="text-xs text-muted-foreground">{fmtDate(r.changed_at)}{r.remarks ? ` · ${r.remarks}` : ""}</div>
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>
            </TabsContent>
            </fieldset>
          </Tabs>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>{editingId && !canEditIPC ? "Close" : "Cancel"}</Button>
            {(!editingId || canEditIPC) && (
              <Button onClick={save} disabled={saving}>{saving ? "Saving…" : editingId ? "Save changes" : "Create"}</Button>
            )}
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
  <Card className="flex flex-col">
    <CardHeader>
      <CardDescription className="text-xs break-words leading-tight">{label}</CardDescription>
    </CardHeader>
    <CardContent className="mt-auto">
      <CardTitle className={`text-xl break-words leading-tight ${cls || ""}`}>{value}</CardTitle>
    </CardContent>
  </Card>
);

const Mini: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <div className="rounded-md bg-muted/40 px-3 py-2">
    <div className="text-xs text-muted-foreground">{label}</div>
    <div className="text-lg font-medium">{value}</div>
  </div>
);

const Field: React.FC<{ label: string; children: React.ReactNode }> = ({ label, children }) => (
  <div className="grid gap-1.5"><Label className="text-xs text-muted-foreground">{label}</Label>{children}</div>
);

// Line items: one row per BOQ/scope item with claimed/verified/approved columns.
const LineItemsEditor: React.FC<{
  rows: IPCLineItem[]; baseCurrency: string; currencyOptions: string[];
  rateFor: (c: string) => number; onChange: (rows: IPCLineItem[]) => void;
}> = ({ rows, baseCurrency, currencyOptions, rateFor, onChange }) => {
  const add = () => onChange([...(rows || []), { description: "", currency: baseCurrency, conversion_rate: 1, claimed: 0, verified: 0, approved: 0 }]);
  const set = (i: number, patch: Partial<IPCLineItem>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="rounded-md border p-2">
      <div className="grid grid-cols-[1fr_80px_repeat(3,90px)_32px] gap-2 border-b pb-1 text-xs text-muted-foreground">
        <span>Description</span><span>Currency</span>
        <span className="text-right">Contractor claimed</span><span className="text-right">GC verified</span><span className="text-right">Employer approved</span><span />
      </div>
      {rows.map((r, i) => (
        <div key={i} className="mt-1 grid grid-cols-[1fr_80px_repeat(3,90px)_32px] items-center gap-2">
          <Input className="h-9" placeholder="Scope / BOQ item" value={r.description || ""} onChange={(e) => set(i, { description: e.target.value })} />
          <select className="flex h-9 rounded-md border border-input bg-background px-2 text-sm" value={r.currency}
            onChange={(e) => { const cur = e.target.value; set(i, { currency: cur, conversion_rate: rateFor(cur) }); }}>
            {Array.from(new Set([...currencyOptions, r.currency].filter(Boolean))).map((c) => (
              <option key={c} value={c}>{c}</option>
            ))}
          </select>
          <Input type="number" className="h-9 text-right" value={r.claimed} onChange={(e) => set(i, { claimed: Number(e.target.value) })} />
          <Input type="number" className="h-9 text-right" value={r.verified} onChange={(e) => set(i, { verified: Number(e.target.value) })} />
          <Input type="number" className="h-9 text-right" value={r.approved} onChange={(e) => set(i, { approved: Number(e.target.value) })} />
          <Button type="button" variant="ghost" size="icon" className="h-9 w-9 text-destructive" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="ghost" size="sm" className="mt-2 h-8" onClick={add}><PlusCircle className="mr-1 h-3.5 w-3.5" />Add line item</Button>
    </div>
  );
};

// Payment records: discrete actual payments made.
const PaymentsEditor: React.FC<{
  rows: IPCPaymentRecord[]; baseCurrency: string; currencyOptions: string[];
  rateFor: (c: string) => number; onChange: (rows: IPCPaymentRecord[]) => void;
}> = ({ rows, baseCurrency, currencyOptions, rateFor, onChange }) => {
  const add = () => onChange([...(rows || []), { payment_date: null, reference: "", method: "", currency: baseCurrency, conversion_rate: 1, amount: 0 }]);
  const set = (i: number, patch: Partial<IPCPaymentRecord>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <div className="rounded-md border p-2">
      <div className="grid grid-cols-[130px_1fr_110px_80px_100px_32px] gap-2 border-b pb-1 text-xs text-muted-foreground">
        <span>Date</span><span>Reference</span><span>Method</span><span>Currency</span><span className="text-right">Amount</span><span />
      </div>
      {rows.map((r, i) => (
        <div key={i} className="mt-1 grid grid-cols-[130px_1fr_110px_80px_100px_32px] items-center gap-2">
          <Input type="date" className="h-9" value={dstr(r.payment_date)} onChange={(e) => set(i, { payment_date: e.target.value ? new Date(e.target.value).toISOString() : null })} />
          <Input className="h-9" placeholder="Cheque / NEFT ref" value={r.reference || ""} onChange={(e) => set(i, { reference: e.target.value })} />
          <Input className="h-9" placeholder="NEFT / RTGS" value={r.method || ""} onChange={(e) => set(i, { method: e.target.value })} />
          <select className="flex h-9 rounded-md border border-input bg-background px-2 text-sm" value={r.currency}
            onChange={(e) => { const cur = e.target.value; set(i, { currency: cur, conversion_rate: rateFor(cur) }); }}>
            {Array.from(new Set([...currencyOptions, r.currency].filter(Boolean))).map((c) => (<option key={c} value={c}>{c}</option>))}
          </select>
          <Input type="number" className="h-9 text-right" value={r.amount} onChange={(e) => set(i, { amount: Number(e.target.value) })} />
          <Button type="button" variant="ghost" size="icon" className="h-9 w-9 text-destructive" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ))}
      <Button type="button" variant="ghost" size="sm" className="mt-2 h-8" onClick={add}><PlusCircle className="mr-1 h-3.5 w-3.5" />Record payment</Button>
    </div>
  );
};

// One deduction component, edited as a grid like Line items so the contractor
// claimed / GC verified / employer approved amounts sit side by side per line.
// Carries an optional master category and description per the component config.
const DeductionLinesEditor: React.FC<{
  title: string; rows: IPCDeductionLine[]; baseCurrency: string;
  currencyOptions: string[]; rateFor: (c: string) => number;
  fieldConfig: ComponentFieldConfig; categories: IPCCategory[];
  onChange: (rows: IPCDeductionLine[]) => void;
}> = ({ title, rows, baseCurrency, currencyOptions, rateFor, fieldConfig, categories, onChange }) => {
  const add = () => onChange([...(rows || []), { category: null, description: "", currency: baseCurrency, conversion_rate: 1, claimed: 0, verified: 0, approved: 0 }]);
  const set = (i: number, patch: Partial<IPCDeductionLine>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const hasCategory = !!fieldConfig.categoryKind;
  const cols = `${hasCategory ? "110px " : ""}${fieldConfig.description ? "minmax(0,1fr) " : ""}68px 74px 74px 74px 28px`;
  const tClaimed = lineTotal(rows, "claimed");
  const tVerified = lineTotal(rows, "verified");
  const tApproved = lineTotal(rows, "approved");
  return (
    <div className="rounded-md border p-2">
      <div className="mb-1 flex items-center justify-between">
        <span className="text-sm font-medium">{title}</span>
        <Button type="button" variant="ghost" size="sm" className="h-7" onClick={add}><PlusCircle className="mr-1 h-3.5 w-3.5" />Add line</Button>
      </div>
      <div className="grid gap-2 border-b pb-1 text-xs text-muted-foreground" style={{ gridTemplateColumns: cols }}>
        {hasCategory && <span>Type</span>}
        {fieldConfig.description && <span>Description</span>}
        <span>Ccy</span>
        <span className="text-right">Claimed</span>
        <span className="text-right">Verified</span>
        <span className="text-right">Approved</span>
        <span />
      </div>
      {rows.map((r, i) => (
        <div key={i} className="mt-1 grid items-center gap-2" style={{ gridTemplateColumns: cols }}>
          {hasCategory && (
            <select className="flex h-9 rounded-md border border-input bg-background px-2 text-sm" value={r.category || ""}
              onChange={(e) => set(i, { category: e.target.value || null })}>
              <option value="">— type —</option>
              {categories.map((c) => <option key={c.id} value={c.code}>{c.name}</option>)}
              {r.category && !categories.some((c) => c.code === r.category) && <option value={r.category}>{r.category}</option>}
            </select>
          )}
          {fieldConfig.description && (
            <Input className="h-9" placeholder="Description / reason" value={r.description || ""}
              onChange={(e) => set(i, { description: e.target.value || null })} />
          )}
          <select className="flex h-9 rounded-md border border-input bg-background px-1 text-sm" value={r.currency}
            onChange={(e) => { const cur = e.target.value; set(i, { currency: cur, conversion_rate: rateFor(cur) }); }}>
            {Array.from(new Set([...currencyOptions, r.currency].filter(Boolean))).map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
          <Input type="number" className="h-9 text-right" value={r.claimed} onChange={(e) => set(i, { claimed: Number(e.target.value) })} />
          <Input type="number" className="h-9 text-right" value={r.verified} onChange={(e) => set(i, { verified: Number(e.target.value) })} />
          <Input type="number" className="h-9 text-right" value={r.approved} onChange={(e) => set(i, { approved: Number(e.target.value) })} />
          <Button type="button" variant="ghost" size="icon" className="h-9 w-9 text-destructive" onClick={() => onChange(rows.filter((_, j) => j !== i))}>
            <Trash2 className="h-4 w-4" />
          </Button>
        </div>
      ))}
      {rows.length > 0 && (
        <div className="mt-2 grid gap-2 border-t pt-2 text-xs font-medium" style={{ gridTemplateColumns: cols }}>
          {hasCategory && <span />}
          <span className="text-muted-foreground">Subtotal ({baseCurrency})</span>
          <span />
          <span className="text-right">{fmt(tClaimed)}</span>
          <span className="text-right">{fmt(tVerified)}</span>
          <span className="text-right">{fmt(tApproved)}</span>
          <span />
        </div>
      )}
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
