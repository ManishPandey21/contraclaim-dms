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
import { Badge } from "@/components/ui/badge";
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
import { CalendarClock, FileSignature, Loader2, PlusCircle, Save, Trash2 } from "lucide-react";
import { toast } from "sonner";
import {
  BGRequiredDate,
  ContractMasterDTO,
  ContractMasterPayload,
  createContractMaster,
  getBGRequiredDates,
  getContractMasterForProject,
  reviseCompletion,
  updateContractMaster,
} from "@/services/contract-master-api";
import { enhancedApi } from "@/services/enhanced-api";

const fmtDate = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");
const toISO = (d: string) => (d ? new Date(d).toISOString() : undefined);
const fmtAmount = (n?: number | null) => (n == null ? "—" : `INR ${Number(n).toLocaleString()}`);
const titleCase = (s: string) => s.split("_").map((w) => w.charAt(0).toUpperCase() + w.slice(1)).join(" ");

interface CForm {
  contract_name: string; contract_code: string; client_name: string; contractor_name: string;
  engineer_name: string; currency: string; original_contract_value: string; contract_start_date: string;
  original_completion_date: string; defect_liability_period_days: string; reporting_period: string;
  week_basis: string;
}
const EMPTY: CForm = {
  contract_name: "", contract_code: "", client_name: "", contractor_name: "", engineer_name: "",
  currency: "INR", original_contract_value: "", contract_start_date: "", original_completion_date: "",
  defect_liability_period_days: "", reporting_period: "", week_basis: "loa_plus_weeks",
};

const ContractMasterPage: React.FC = () => {
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [projectId, setProjectId] = useState("");
  const [master, setMaster] = useState<ContractMasterDTO | null>(null);
  const [form, setForm] = useState<CForm>({ ...EMPTY });
  // Multi-currency rows (strings for inputs). Empty = single-currency contract.
  const [currencyRows, setCurrencyRows] = useState<
    { currency: string; rate: string; value: string }[]
  >([]);
  const [bgDates, setBgDates] = useState<BGRequiredDate[]>([]);
  const [reviseDate, setReviseDate] = useState("");
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);

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

  const fillForm = (m: ContractMasterDTO | null) => {
    if (!m) { setForm({ ...EMPTY }); setCurrencyRows([]); return; }
    setCurrencyRows(
      (m.contract_currencies || []).map((c) => ({
        currency: c.currency || "",
        rate: c.conversion_rate != null ? String(c.conversion_rate) : "",
        value: c.contract_value != null ? String(c.contract_value) : "",
      })),
    );
    setForm({
      contract_name: m.contract_name || "", contract_code: m.contract_code || "",
      client_name: m.client_name || "", contractor_name: m.contractor_name || "",
      engineer_name: m.engineer_name || "", currency: m.currency || "INR",
      original_contract_value: m.original_contract_value != null ? String(m.original_contract_value) : "",
      contract_start_date: m.contract_start_date ? m.contract_start_date.slice(0, 10) : "",
      original_completion_date: m.original_completion_date ? m.original_completion_date.slice(0, 10) : "",
      defect_liability_period_days: m.defect_liability_period_days != null ? String(m.defect_liability_period_days) : "",
      reporting_period: m.reporting_period || "",
      week_basis: m.week_basis || "loa_plus_weeks",
    });
  };

  const loadMaster = useCallback(async (pid: string) => {
    setLoading(true);
    try {
      const m = await getContractMasterForProject(pid);
      setMaster(m);
      fillForm(m);
      setBgDates(m ? await getBGRequiredDates(m.id) : []);
    } catch {
      toast.error("Failed to load contract master");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (projectId) void loadMaster(projectId);
    else { setMaster(null); fillForm(null); setBgDates([]); }
  }, [projectId, loadMaster]);

  const save = async () => {
    if (!projectId) { toast.error("Select a project"); return; }
    setSaving(true);
    try {
      const payload: ContractMasterPayload = {
        project_id: projectId,
        contract_name: form.contract_name || undefined,
        contract_code: form.contract_code || undefined,
        client_name: form.client_name || undefined,
        contractor_name: form.contractor_name || undefined,
        engineer_name: form.engineer_name || undefined,
        currency: form.currency || "INR",
        original_contract_value: form.original_contract_value ? Number(form.original_contract_value) : undefined,
        contract_start_date: toISO(form.contract_start_date),
        original_completion_date: toISO(form.original_completion_date),
        defect_liability_period_days: form.defect_liability_period_days ? Number(form.defect_liability_period_days) : undefined,
        reporting_period: form.reporting_period || undefined,
        week_basis: form.week_basis || undefined,
      };
      // Only send valid currency rows (code + positive rate). An empty list
      // clears the breakdown back to single-currency.
      const rows = currencyRows
        .filter((r) => r.currency.trim() && Number(r.rate) > 0)
        .map((r) => ({
          currency: r.currency.trim().toUpperCase(),
          conversion_rate: Number(r.rate),
          contract_value: r.value ? Number(r.value) : undefined,
        }));
      payload.contract_currencies = rows;
      if (master) await updateContractMaster(master.id, payload);
      else await createContractMaster(payload);
      toast.success("Contract master saved");
      await loadMaster(projectId);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to save");
    } finally {
      setSaving(false);
    }
  };

  const onRevise = async () => {
    if (!master || !reviseDate) { toast.error("Pick a revised completion date"); return; }
    setSaving(true);
    try {
      await reviseCompletion(master.id, new Date(reviseDate).toISOString());
      toast.success("Completion revised — bank-guarantee required dates recomputed");
      setReviseDate("");
      await loadMaster(projectId);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to revise completion");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center gap-2">
        <FileSignature className="h-6 w-6 text-blue-600" />
        <div>
          <h1 className="text-2xl font-bold">Contract Master</h1>
          <p className="text-sm text-muted-foreground">
            One source for contract value, completion dates and BG-validity rules — drives the Variation and Bank Guarantee registers.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Project</CardTitle>
          <CardDescription>Select a project to manage its contract master.</CardDescription>
        </CardHeader>
        <CardContent>
          <Select value={projectId} onValueChange={setProjectId}>
            <SelectTrigger className="w-72"><SelectValue placeholder="Select project" /></SelectTrigger>
            <SelectContent>{projects.map((p) => <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>)}</SelectContent>
          </Select>
        </CardContent>
      </Card>

      {projectId && (
        loading ? (
          <div className="flex items-center justify-center py-10 text-muted-foreground">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading…
          </div>
        ) : (
          <>
            <Card>
              <CardHeader>
                <div className="flex items-center justify-between">
                  <CardTitle>{master ? "Edit contract master" : "Create contract master"}</CardTitle>
                  {master && (
                    <div className="flex flex-wrap gap-2 text-sm">
                      <Badge variant="outline">Original: {fmtAmount(master.original_contract_value)}</Badge>
                      <Badge className="bg-blue-600">Current: {fmtAmount(master.current_contract_value)}</Badge>
                      {master.contract_currencies && master.contract_currencies.length > 0 && (
                        <Badge className="bg-emerald-600">
                          Total ({master.currency}): {fmtAmount(master.total_contract_value_base)}
                        </Badge>
                      )}
                    </div>
                  )}
                </div>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid grid-cols-2 gap-3 md:grid-cols-3">
                  <div><Label>Contract name</Label><Input value={form.contract_name} onChange={(e) => setForm({ ...form, contract_name: e.target.value })} /></div>
                  <div><Label>Contract code</Label><Input value={form.contract_code} onChange={(e) => setForm({ ...form, contract_code: e.target.value })} /></div>
                  <div><Label>Currency</Label><Input value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} /></div>
                  <div><Label>Client</Label><Input value={form.client_name} onChange={(e) => setForm({ ...form, client_name: e.target.value })} /></div>
                  <div><Label>Contractor</Label><Input value={form.contractor_name} onChange={(e) => setForm({ ...form, contractor_name: e.target.value })} /></div>
                  <div><Label>Engineer / PM</Label><Input value={form.engineer_name} onChange={(e) => setForm({ ...form, engineer_name: e.target.value })} /></div>
                  <div><Label>Original contract value</Label><Input type="number" value={form.original_contract_value} onChange={(e) => setForm({ ...form, original_contract_value: e.target.value })} /></div>
                  <div><Label>Contract start date (LOA)</Label><Input type="date" value={form.contract_start_date} onChange={(e) => setForm({ ...form, contract_start_date: e.target.value })} /></div>
                  <div><Label>Original completion date</Label><Input type="date" value={form.original_completion_date} onChange={(e) => setForm({ ...form, original_completion_date: e.target.value })} /></div>
                  <div><Label>Defect liability (days)</Label><Input type="number" value={form.defect_liability_period_days} onChange={(e) => setForm({ ...form, defect_liability_period_days: e.target.value })} /></div>
                  <div><Label>Reporting period</Label><Input value={form.reporting_period} onChange={(e) => setForm({ ...form, reporting_period: e.target.value })} placeholder="monthly" /></div>
                  <div>
                    <Label>Key-date week basis</Label>
                    <select
                      className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
                      value={form.week_basis}
                      onChange={(e) => setForm({ ...form, week_basis: e.target.value })}
                    >
                      <option value="loa_plus_weeks">LOA + (weeks × 7) — contractual date</option>
                      <option value="loa_plus_weeks_minus_1">LOA + ((weeks − 1) × 7) — FIDIC style</option>
                    </select>
                  </div>
                </div>

                <div className="rounded-md border p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <div>
                      <Label className="text-sm font-medium">Contract currencies</Label>
                      <p className="text-xs text-muted-foreground">
                        For a multi-currency contract, add each currency with its
                        award-fixed conversion rate to the base ({form.currency || "base"}).
                        Leave empty for a single-currency contract.
                      </p>
                    </div>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        setCurrencyRows((r) => [...r, { currency: "", rate: "", value: "" }])
                      }
                    >
                      <PlusCircle className="mr-2 h-4 w-4" /> Add currency
                    </Button>
                  </div>

                  {currencyRows.length > 0 && (
                    <div className="space-y-2">
                      <div className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2 text-xs text-muted-foreground">
                        <span>Currency</span>
                        <span>Rate → {form.currency || "base"}</span>
                        <span>Value (in currency)</span>
                        <span />
                      </div>
                      {currencyRows.map((row, i) => (
                        <div key={i} className="grid grid-cols-[1fr_1fr_1fr_auto] gap-2">
                          <Input
                            placeholder="USD"
                            value={row.currency}
                            onChange={(e) =>
                              setCurrencyRows((r) =>
                                r.map((x, j) => (j === i ? { ...x, currency: e.target.value } : x)),
                              )
                            }
                          />
                          <Input
                            type="number"
                            placeholder="83.0"
                            value={row.rate}
                            onChange={(e) =>
                              setCurrencyRows((r) =>
                                r.map((x, j) => (j === i ? { ...x, rate: e.target.value } : x)),
                              )
                            }
                          />
                          <Input
                            type="number"
                            placeholder="10000"
                            value={row.value}
                            onChange={(e) =>
                              setCurrencyRows((r) =>
                                r.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)),
                              )
                            }
                          />
                          <Button
                            type="button"
                            variant="ghost"
                            size="icon"
                            className="h-10 w-10 text-destructive"
                            onClick={() => setCurrencyRows((r) => r.filter((_, j) => j !== i))}
                          >
                            <Trash2 className="h-4 w-4" />
                          </Button>
                        </div>
                      ))}
                      <div className="pt-1 text-sm font-medium">
                        Total contract value ({form.currency || "base"}):{" "}
                        {fmtAmount(
                          currencyRows.reduce(
                            (s, r) => s + (Number(r.value) || 0) * (Number(r.rate) || 0),
                            0,
                          ),
                        )}
                      </div>
                    </div>
                  )}
                </div>

                <Button onClick={save} disabled={saving}>
                  {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                  {master ? "Save changes" : "Create"}
                </Button>
              </CardContent>
            </Card>

            {master && (
              <div className="grid gap-4 lg:grid-cols-2">
                <Card>
                  <CardHeader>
                    <CardTitle className="flex items-center gap-2 text-base"><CalendarClock className="h-4 w-4" />Revise completion (EOT)</CardTitle>
                    <CardDescription>
                      Moving the completion date recomputes every bank-guarantee required-up-to date automatically.
                    </CardDescription>
                  </CardHeader>
                  <CardContent className="space-y-3">
                    <div className="grid grid-cols-2 gap-3 text-sm">
                      <div><span className="text-muted-foreground">Original:</span> {fmtDate(master.original_completion_date)}</div>
                      <div><span className="text-muted-foreground">Revised:</span> {fmtDate(master.revised_completion_date)}</div>
                      <div><span className="text-muted-foreground">Effective:</span> {fmtDate(master.effective_completion_date)}</div>
                      <div><span className="text-muted-foreground">DLP end:</span> {fmtDate(master.dlp_end_date)}</div>
                    </div>
                    <div className="flex items-end gap-2">
                      <div className="flex-1">
                        <Label>New revised completion date</Label>
                        <Input type="date" value={reviseDate} onChange={(e) => setReviseDate(e.target.value)} />
                      </div>
                      <Button onClick={onRevise} disabled={saving || !reviseDate}>Revise</Button>
                    </div>
                  </CardContent>
                </Card>

                <Card>
                  <CardHeader>
                    <CardTitle className="text-base">BG required-up-to dates</CardTitle>
                    <CardDescription>Computed per BG type from the validity rules.</CardDescription>
                  </CardHeader>
                  <CardContent>
                    <Table>
                      <TableHeader>
                        <TableRow><TableHead>BG Type</TableHead><TableHead>Basis</TableHead><TableHead>Offset</TableHead><TableHead>Required Up To</TableHead></TableRow>
                      </TableHeader>
                      <TableBody>
                        {bgDates.map((d) => (
                          <TableRow key={d.bg_type}>
                            <TableCell className="text-xs">{titleCase(d.bg_type)}</TableCell>
                            <TableCell className="text-xs">{titleCase(d.basis)}</TableCell>
                            <TableCell className="text-xs">{d.offset_days}d</TableCell>
                            <TableCell className="text-xs">{fmtDate(d.required_up_to)}</TableCell>
                          </TableRow>
                        ))}
                      </TableBody>
                    </Table>
                  </CardContent>
                </Card>
              </div>
            )}
          </>
        )
      )}
    </div>
  );
};

export default ContractMasterPage;
