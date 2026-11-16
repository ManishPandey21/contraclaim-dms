import React, { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
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
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AlertTriangle,
  ChevronDown,
  ClipboardList,
  Clock,
  Download,
  Edit,
  Loader2,
  MoreHorizontal,
  PlusCircle,
  ShieldCheck,
  Sparkles,
  Trash2,
} from "lucide-react";
import { toast } from "sonner";
import {
  ClaimDTO,
  ClaimPayload,
  ClaimStatus,
  ClaimType,
  createClaim,
  deleteClaim,
  downloadEvidenceBundle,
  getClaims,
  setClaimStatus,
  updateClaim,
} from "@/services/claims-api";
import { enhancedApi } from "@/services/enhanced-api";
import ClaimApprovalDialog from "@/components/claims/ClaimApprovalDialog";
import ClaimAssessmentDialog from "@/components/claims/ClaimAssessmentDialog";
import ClaimTaskDialog from "@/components/claims/ClaimTaskDialog";
import { getUpcoming } from "@/services/sla-api";
import { useRegisterProjectScope } from "@/hooks/useRegisterProjectScope";
import { scopeRefusalMessage } from "@/services/active-scope";
import {
  CLAIM_STATUS_LABEL,
  NOTICE_BADGE_VARIANT,
  apiErrorDetail,
  buildSlaStateMap,
  describeNotice,
  parseClauseList,
  type ClaimSla,
} from "@/lib/claims-helpers";
import ClaimStatusPill from "@/components/claims/ClaimStatusPill";
import ClaimSignalsStrip from "@/components/claim-bench/ClaimSignalsStrip";
import { useClaimBenchOn } from "@/hooks/useClaimBenchOn";
import { formatCrore, formatDateIN, formatINR, fromDateInput, toDateInput } from "@/lib/format-in";

const CLAIM_TYPES: { value: ClaimType; label: string }[] = [
  { value: "eot", label: "Extension of Time" },
  { value: "variation", label: "Variation" },
  { value: "payment_ipc", label: "Payment / IPC" },
  { value: "loss_expense", label: "Loss & Expense" },
  { value: "acceleration", label: "Acceleration" },
  { value: "defect", label: "Defect" },
  { value: "other", label: "Other" },
];

const CLAIM_STATUSES: { value: ClaimStatus; label: string }[] = [
  { value: "potential", label: "Potential" },
  { value: "draft", label: "Draft" },
  { value: "notified", label: "Notified" },
  { value: "submitted", label: "Submitted" },
  { value: "under_review", label: "Under Review" },
  { value: "agreed", label: "Agreed" },
  { value: "rejected", label: "Rejected" },
  { value: "disputed", label: "Disputed" },
  { value: "closed", label: "Closed" },
];

const typeLabel = (t: ClaimType) => CLAIM_TYPES.find((x) => x.value === t)?.label || t;

interface ClaimForm {
  title: string;
  type: ClaimType;
  claim_ref: string;
  description: string;
  amount_claimed?: number;
  currency: string;
  event_date: string;
  awareness_date: string;
  notice_date: string;
  response_due_date: string;
  eot_days_claimed?: number;
  contract_clauses: string;
  project_id: string;
  status: ClaimStatus;
}

const EMPTY_FORM: ClaimForm = {
  title: "",
  type: "other",
  claim_ref: "",
  description: "",
  amount_claimed: undefined,
  currency: "INR",
  event_date: "",
  awareness_date: "",
  notice_date: "",
  response_due_date: "",
  eot_days_claimed: undefined,
  contract_clauses: "",
  project_id: "",
  status: "draft",
};

const ClaimsRegisterPage: React.FC = () => {
  const [claims, setClaims] = useState<ClaimDTO[]>([]);
  const [projects, setProjects] = useState<{ id: string; name: string }[]>([]);
  const [typeFilter, setTypeFilter] = useState<string>("all");
  const [statusFilter, setStatusFilter] = useState<string>("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editingClaim, setEditingClaim] = useState<ClaimDTO | null>(null);
  // A claim with a Claim Bench board takes its amount and days from the heads (D-14, 4B-17).
  const fromBoard = Boolean(editingId && editingClaim?.bench_stage);
  const [deleteTarget, setDeleteTarget] = useState<ClaimDTO | null>(null);
  const [form, setForm] = useState<ClaimForm>({ ...EMPTY_FORM });
  const [saving, setSaving] = useState(false);
  const [approvalClaim, setApprovalClaim] = useState<ClaimDTO | null>(null);
  const [assessClaim, setAssessClaim] = useState<ClaimDTO | null>(null);
  // The free-text assessment is retired where the Claim Bench is on.
  const benchOn = useClaimBenchOn();
  const [taskClaim, setTaskClaim] = useState<ClaimDTO | null>(null);
  const [slaMap, setSlaMap] = useState<Record<string, ClaimSla>>({});
  const [exportingId, setExportingId] = useState<string | null>(null);
  const navigate = useNavigate();
  // CL-4A: the server pins the list to the navbar project; new claims are filed in it.
  const { selectedProjectId, tenantLoading } = useRegisterProjectScope();

  const load = useCallback(async () => {
    try {
      const params: Record<string, string> = {};
      if (typeFilter !== "all") params.type = typeFilter;
      if (statusFilter !== "all") params.status = statusFilter;
      setClaims(await getClaims(params));
    } catch (error) {
      toast.error(scopeRefusalMessage(error, "claim") || "Failed to load claims");
    }
    try {
      setSlaMap(buildSlaStateMap(await getUpcoming({ days: 30 })));
    } catch {
      /* SLA badges are best-effort */
    }
  }, [typeFilter, statusFilter]);

  useEffect(() => {
    if (!tenantLoading) void load();
  }, [load, tenantLoading]);

  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const ps = await enhancedApi.getProjects();
        if (active) {
          setProjects(
            (ps || []).map((p: any) => ({ id: String(p.id || p._id || ""), name: p.name || "Project" }))
          );
        }
      } catch {
        /* projects optional */
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  const openCreate = () => {
    setEditingId(null);
    setEditingClaim(null);
    setForm({ ...EMPTY_FORM, project_id: selectedProjectId });
    setDialogOpen(true);
  };

  const openEdit = (c: ClaimDTO) => {
    setEditingId(c.id);
    setEditingClaim(c);
    setForm({
      title: c.title,
      type: c.type,
      claim_ref: c.claim_ref || "",
      description: c.description || "",
      amount_claimed: c.amount_claimed ?? undefined,
      currency: c.currency || "INR",
      event_date: toDateInput(c.event_date),
      awareness_date: toDateInput(c.awareness_date),
      notice_date: toDateInput(c.notice_date),
      response_due_date: toDateInput(c.response_due_date),
      eot_days_claimed: c.eot_days_claimed ?? undefined,
      contract_clauses: c.contract_clauses.join(", "),
      project_id: c.project_id || "",
      status: c.status,
    });
    setDialogOpen(true);
  };

  const submit = async () => {
    if (!form.title.trim()) {
      toast.error("Title is required");
      return;
    }
    try {
      setSaving(true);
      // When editing, an emptied field is sent as null so the server clears it
      // (4B-12); left out, the old value would silently stay.
      const cleared = editingId ? null : undefined;
      // With a Claim Bench board the amount and days come from its heads (D-14); the
      // server refuses a change, so they are not sent.
      const payload: ClaimPayload = {
        title: form.title.trim(),
        type: form.type,
        claim_ref: form.claim_ref || undefined,
        description: form.description || cleared,
        amount_claimed: fromBoard ? undefined : form.amount_claimed,
        currency: form.currency.trim().toUpperCase() || undefined,
        event_date: fromDateInput(form.event_date) ?? cleared,
        awareness_date: fromDateInput(form.awareness_date) ?? cleared,
        notice_date: fromDateInput(form.notice_date) ?? cleared,
        response_due_date: fromDateInput(form.response_due_date) ?? cleared,
        eot_days_claimed: fromBoard ? undefined : form.eot_days_claimed,
        contract_clauses: parseClauseList(form.contract_clauses),
        project_id: form.project_id || undefined,
        status: form.status,
      };
      if (editingId) await updateClaim(editingId, payload);
      else await createClaim(payload);
      await load();
      toast.success(editingId ? "Claim updated" : "Claim created");
      setDialogOpen(false);
    } catch (error) {
      toast.error(scopeRefusalMessage(error, "claim") || apiErrorDetail(error) || "Failed to save claim");
    } finally {
      setSaving(false);
    }
  };

  const changeStatus = async (c: ClaimDTO, statusValue: ClaimStatus) => {
    try {
      await setClaimStatus(c.id, statusValue);
      await load();
      toast.success(`${c.claim_ref || "Claim"} moved to ${CLAIM_STATUS_LABEL[statusValue]}`);
    } catch (error) {
      // The server explains a refused move (scope, or a step the lifecycle does not allow).
      toast.error(scopeRefusalMessage(error, "claim") || apiErrorDetail(error) || "Failed to update status");
    }
  };

  const exportBundle = async (c: ClaimDTO) => {
    try {
      setExportingId(c.id);
      await downloadEvidenceBundle(c.id);
      toast.success("Evidence bundle downloaded");
    } catch {
      toast.error("Failed to export evidence bundle");
    } finally {
      setExportingId(null);
    }
  };

  const noticeAttention = claims.reduce(
    (acc, c) => {
      const state = c.notice_assessment?.state;
      if (state === "overdue") acc.overdue += 1;
      else if (state === "due_soon") acc.dueSoon += 1;
      else if (state === "served_late") acc.late += 1;
      return acc;
    },
    { overdue: 0, dueSoon: 0, late: 0 },
  );

  const remove = async (c: ClaimDTO) => {
    try {
      await deleteClaim(c.id);
      await load();
      toast.success("Claim deleted");
    } catch (error) {
      toast.error(scopeRefusalMessage(error, "claim") || apiErrorDetail(error) || "Failed to delete claim");
    }
  };

  return (
    <div className="container mx-auto p-6">
      <div className="flex justify-between items-center mb-6">
        <h1 className="text-2xl font-bold">Claim Register</h1>
        <Button onClick={openCreate}>
          <PlusCircle className="mr-2 h-4 w-4" />
          New Claim
        </Button>
      </div>

      <ClaimSignalsStrip onRaised={() => void load()} />

      <Card>
        <CardHeader>
          <CardTitle>Claims</CardTitle>
          <CardDescription>Track EOT, variation, payment and loss/expense claims.</CardDescription>
          {noticeAttention.overdue + noticeAttention.dueSoon + noticeAttention.late > 0 && (
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1 pt-2 text-sm" role="status">
              {noticeAttention.overdue > 0 && (
                <span className="font-medium text-red-800">
                  <AlertTriangle className="mr-1 inline h-4 w-4" />
                  {noticeAttention.overdue} notice{noticeAttention.overdue === 1 ? "" : "s"} overdue
                </span>
              )}
              {noticeAttention.dueSoon > 0 && (
                <span className="font-medium text-amber-800">
                  <Clock className="mr-1 inline h-4 w-4" />
                  {noticeAttention.dueSoon} due within 7 days
                </span>
              )}
              {noticeAttention.late > 0 && (
                <span className="text-muted-foreground">
                  {noticeAttention.late} served after the period
                </span>
              )}
            </p>
          )}
          <div className="flex gap-3 pt-3">
            <Select value={typeFilter} onValueChange={setTypeFilter}>
              <SelectTrigger className="w-48"><SelectValue placeholder="Type" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All types</SelectItem>
                {CLAIM_TYPES.map((t) => (
                  <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            <Select value={statusFilter} onValueChange={setStatusFilter}>
              <SelectTrigger className="w-48"><SelectValue placeholder="Status" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All statuses</SelectItem>
                {CLAIM_STATUSES.map((s) => (
                  <SelectItem key={s.value} value={s.value}>{s.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Ref</TableHead>
                <TableHead>Title</TableHead>
                <TableHead>Type</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Notice</TableHead>
                <TableHead className="text-right">Amount</TableHead>
                <TableHead>Response due</TableHead>
                <TableHead className="text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {claims.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={8} className="text-center text-muted-foreground py-8">
                    {typeFilter !== "all" || statusFilter !== "all"
                      ? "No claims match these filters."
                      : "No claims yet. Use New Claim to record the first one."}
                  </TableCell>
                </TableRow>
              ) : (
                claims.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-mono text-xs">{c.claim_ref || "—"}</TableCell>
                    <TableCell className="font-medium">{c.title}</TableCell>
                    <TableCell>{typeLabel(c.type)}</TableCell>
                    <TableCell>
                      {c.allowed_status_transitions.length === 0 ? (
                        <ClaimStatusPill status={c.status} />
                      ) : (
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <button
                              type="button"
                              className="cursor-pointer rounded-full focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                              aria-label={`Status ${CLAIM_STATUS_LABEL[c.status] || c.status}. Change status`}
                            >
                              <ClaimStatusPill status={c.status}>
                                <ChevronDown className="h-3 w-3" aria-hidden />
                              </ClaimStatusPill>
                            </button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="start">
                            {c.allowed_status_transitions.map((next) => (
                              <DropdownMenuItem key={next} onClick={() => changeStatus(c, next)}>
                                Move to {CLAIM_STATUS_LABEL[next] || next}
                              </DropdownMenuItem>
                            ))}
                          </DropdownMenuContent>
                        </DropdownMenu>
                      )}
                    </TableCell>
                    <TableCell>
                      {(() => {
                        const summary = describeNotice(c.notice_assessment);
                        if (!summary) {
                          return <span className="text-xs text-muted-foreground">No event date</span>;
                        }
                        return (
                          <span title={summary.sentence}>
                            <Badge variant={NOTICE_BADGE_VARIANT[summary.tone]} className="whitespace-nowrap">
                              {summary.label}
                            </Badge>
                          </span>
                        );
                      })()}
                    </TableCell>
                    <TableCell className="whitespace-nowrap text-right tabular-nums" title={formatCrore(c.amount_claimed, c.currency)}>
                      {formatINR(c.amount_claimed, c.currency)}
                    </TableCell>
                    <TableCell>
                      <div className="flex items-center gap-2">
                        <span className="whitespace-nowrap">{formatDateIN(c.response_due_date)}</span>
                        {slaMap[c.id] &&
                          (slaMap[c.id].state === "breached" ? (
                            <Badge variant="danger" className="gap-1">
                              <AlertTriangle className="h-3 w-3" />
                              {Math.abs(slaMap[c.id].days_remaining)}d over
                            </Badge>
                          ) : (
                            <Badge variant="outline" className="gap-1 border-amber-500 text-amber-600">
                              <Clock className="h-3 w-3" />
                              {slaMap[c.id].days_remaining}d
                            </Badge>
                          ))}
                      </div>
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button variant="outline" size="sm" onClick={() => navigate(`/claims/${c.id}`)}>
                          Open
                        </Button>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="icon" className="h-8 w-8" aria-label={`More actions for ${c.claim_ref || c.title}`}>
                              {exportingId === c.id ? <Loader2 className="h-4 w-4 animate-spin" /> : <MoreHorizontal className="h-4 w-4" />}
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end">
                            <DropdownMenuItem onClick={() => openEdit(c)}>
                              <Edit className="mr-2 h-4 w-4" /> Edit details
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => setTaskClaim(c)}>
                              <ClipboardList className="mr-2 h-4 w-4" /> New task
                            </DropdownMenuItem>
                            {!benchOn && (
                              <DropdownMenuItem onClick={() => setAssessClaim(c)}>
                                <Sparkles className="mr-2 h-4 w-4" /> AI assessment
                              </DropdownMenuItem>
                            )}
                            <DropdownMenuItem onClick={() => setApprovalClaim(c)}>
                              <ShieldCheck className="mr-2 h-4 w-4" /> Approval
                            </DropdownMenuItem>
                            <DropdownMenuItem disabled={exportingId === c.id} onClick={() => exportBundle(c)}>
                              <Download className="mr-2 h-4 w-4" /> Export evidence bundle
                            </DropdownMenuItem>
                            <DropdownMenuItem className="text-destructive focus:text-destructive" onClick={() => setDeleteTarget(c)}>
                              <Trash2 className="mr-2 h-4 w-4" /> Delete
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              )}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <AlertDialog open={deleteTarget !== null} onOpenChange={(o) => !o && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete claim?</AlertDialogTitle>
            <AlertDialogDescription>
              This permanently deletes &quot;{deleteTarget?.title}&quot;.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              onClick={() => {
                if (deleteTarget) void remove(deleteTarget);
                setDeleteTarget(null);
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{editingId ? "Edit Claim" : "New Claim"}</DialogTitle>
            <DialogDescription>Capture the claim details and link it to a project.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>Title</Label>
              <Input value={form.title} onChange={(e) => setForm({ ...form, title: e.target.value })} placeholder="e.g. EOT — monsoon delay" />
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Type</Label>
                <Select value={form.type} onValueChange={(v) => setForm({ ...form, type: v as ClaimType })}>
                  <SelectTrigger><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {CLAIM_TYPES.map((t) => (
                      <SelectItem key={t.value} value={t.value}>{t.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div>
                <Label>Reference</Label>
                <Input value={form.claim_ref} onChange={(e) => setForm({ ...form, claim_ref: e.target.value })} placeholder="MRP3/CON/EOT/001" />
              </div>
            </div>
            <fieldset className="rounded-md border p-3">
              <legend className="px-1 text-sm font-medium">Event and notice</legend>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <div>
                  <Label htmlFor="claim-event-date">Event date</Label>
                  <Input id="claim-event-date" type="date" value={form.event_date} onChange={(e) => setForm({ ...form, event_date: e.target.value })} />
                </div>
                <div>
                  <Label htmlFor="claim-awareness-date">Became aware on</Label>
                  <Input id="claim-awareness-date" type="date" value={form.awareness_date} onChange={(e) => setForm({ ...form, awareness_date: e.target.value })} />
                </div>
                <div>
                  <Label htmlFor="claim-notice-date">Notice served on</Label>
                  <Input id="claim-notice-date" type="date" value={form.notice_date} onChange={(e) => setForm({ ...form, notice_date: e.target.value })} />
                </div>
              </div>
              <p className="pt-2 text-xs text-muted-foreground">
                The notice period is counted from the awareness date when given, otherwise from the event date.
                Leave the notice date empty until notice has actually been served.
              </p>
            </fieldset>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div>
                <Label htmlFor="claim-amount">Amount claimed</Label>
                <Input
                  id="claim-amount"
                  type="number"
                  min={0}
                  disabled={fromBoard}
                  aria-describedby={fromBoard ? "claim-from-board" : undefined}
                  value={form.amount_claimed ?? ""}
                  onChange={(e) =>
                    setForm({ ...form, amount_claimed: e.target.value === "" ? undefined : Number(e.target.value) })
                  }
                />
                {form.amount_claimed != null && (
                  <p className="pt-1 text-xs text-muted-foreground">{formatCrore(form.amount_claimed, form.currency)}</p>
                )}
                {fromBoard && (
                  <p id="claim-from-board" className="pt-1 text-xs text-muted-foreground">
                    Comes from the heads on the Claim Bench. Change it there.
                  </p>
                )}
              </div>
              <div>
                <Label htmlFor="claim-currency">Currency</Label>
                <Input id="claim-currency" maxLength={3} value={form.currency} onChange={(e) => setForm({ ...form, currency: e.target.value })} />
              </div>
              <div>
                <Label htmlFor="claim-eot-days">EOT days claimed</Label>
                <Input
                  id="claim-eot-days"
                  type="number"
                  min={0}
                  step={1}
                  disabled={fromBoard}
                  aria-describedby={fromBoard ? "claim-from-board" : undefined}
                  value={form.eot_days_claimed ?? ""}
                  onChange={(e) =>
                    setForm({ ...form, eot_days_claimed: e.target.value === "" ? undefined : Math.trunc(Number(e.target.value)) })
                  }
                />
              </div>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div>
                <Label htmlFor="claim-clauses">Contract clauses relied on</Label>
                <Input id="claim-clauses" value={form.contract_clauses} onChange={(e) => setForm({ ...form, contract_clauses: e.target.value })} placeholder="e.g. 8.4, 20.1" />
              </div>
              <div>
                <Label htmlFor="claim-response-due">Response due</Label>
                <Input id="claim-response-due" type="date" value={form.response_due_date} onChange={(e) => setForm({ ...form, response_due_date: e.target.value })} />
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Project</Label>
                <Select value={form.project_id} onValueChange={(v) => setForm({ ...form, project_id: v })}>
                  <SelectTrigger><SelectValue placeholder="Select project" /></SelectTrigger>
                  <SelectContent>
                    {projects.map((p) => (
                      <SelectItem key={p.id} value={p.id}>{p.name}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              {editingId && (
                <div>
                  <Label>Status</Label>
                  <Select value={form.status} onValueChange={(v) => setForm({ ...form, status: v as ClaimStatus })}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {[form.status, ...(editingClaim?.allowed_status_transitions ?? [])]
                        .filter((value, index, all) => all.indexOf(value) === index)
                        .map((value) => (
                          <SelectItem key={value} value={value}>{CLAIM_STATUS_LABEL[value] || value}</SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>
              )}
            </div>
            <div>
              <Label>Description</Label>
              <Textarea value={form.description} onChange={(e) => setForm({ ...form, description: e.target.value })} rows={3} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)} disabled={saving}>Cancel</Button>
            <Button onClick={submit} disabled={saving || !form.title.trim()}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <PlusCircle className="mr-2 h-4 w-4" />}
              {editingId ? "Save changes" : "Create claim"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <ClaimApprovalDialog
        claimId={approvalClaim?.id ?? null}
        claimTitle={approvalClaim?.title}
        open={approvalClaim !== null}
        onOpenChange={(o) => {
          if (!o) setApprovalClaim(null);
        }}
      />

      <ClaimAssessmentDialog
        claimId={assessClaim?.id ?? null}
        claimTitle={assessClaim?.title}
        open={assessClaim !== null}
        onOpenChange={(o) => {
          if (!o) setAssessClaim(null);
        }}
      />

      <ClaimTaskDialog
        claim={taskClaim}
        open={taskClaim !== null}
        onOpenChange={(o) => {
          if (!o) setTaskClaim(null);
        }}
      />
    </div>
  );
};

export default ClaimsRegisterPage;
