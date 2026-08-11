import React, { useCallback, useEffect, useMemo, useState } from "react";
import { Download, FileCheck2, History, Loader2, LockKeyhole, PlusCircle, Upload } from "lucide-react";
import { toast } from "sonner";
import CsvImportDialog from "@/components/registers/CsvImportDialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Textarea } from "@/components/ui/textarea";
import {
  createEOTDetermination,
  createEOTSubmissionRevision,
  downloadEOTDeterminationTemplate,
  downloadEOTSubmissionTemplate,
  EOTDeterminationResult,
  EOTDeterminationStatus,
  EOTDeterminationDTO,
  EOTSubmissionRevisionDTO,
  exportKeyDateWorkflow,
  freezeEOTDetermination,
  freezeOriginalKeyDates,
  getKeyDateWorkflow,
  importEOTDeterminationCsv,
  importEOTSubmissionCsv,
  KeyDateWorkflowSummaryDTO,
  lockEOTSubmissionRevision,
  MilestoneDTO,
  previewEOTDeterminationCsv,
  previewEOTSubmissionCsv,
  updateEOTSubmissionRevision,
  updateEOTDetermination,
} from "@/services/key-dates-api";

const fmt = (value?: string | null) => value ? new Date(value).toLocaleDateString() : "—";
const iso = (value: string) => value ? new Date(value).toISOString() : undefined;

type SubmissionRow = {
  include: boolean;
  submittedDate: string;
  claimedDays: string;
  remarks: string;
};
type DeterminationRow = {
  grantDate: string;
  grantedDays: string;
  result: EOTDeterminationResult;
  remarks: string;
};

const blankSubmission = {
  eotReference: "", submissionDate: "", letterReference: "", cutoffDate: "", remarks: "", status: "submitted" as const,
};

function downloadBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

const MiniStat: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div className="rounded-md border bg-muted/20 p-3">
    <p className="text-xs text-muted-foreground">{label}</p>
    <p className="mt-1 font-semibold">{value ?? "—"}</p>
  </div>
);

interface Props {
  projectId: string;
  milestones: MilestoneDTO[];
  onChanged: () => Promise<void> | void;
  onBaselineStatusChange?: (frozen: boolean) => void;
}

const KeyDateRevisionWorkflow: React.FC<Props> = ({ projectId, milestones, onChanged, onBaselineStatusChange }) => {
  const [workflow, setWorkflow] = useState<KeyDateWorkflowSummaryDTO | null>(null);
  const [busy, setBusy] = useState(false);
  const [submissionOpen, setSubmissionOpen] = useState(false);
  const [editingSubmission, setEditingSubmission] = useState<EOTSubmissionRevisionDTO | null>(null);
  const [submissionForm, setSubmissionForm] = useState({ ...blankSubmission });
  const [submissionRows, setSubmissionRows] = useState<Record<string, SubmissionRow>>({});
  const [determinationOpen, setDeterminationOpen] = useState(false);
  const [editingDetermination, setEditingDetermination] = useState<EOTDeterminationDTO | null>(null);
  const [selectedSubmissionIds, setSelectedSubmissionIds] = useState<string[]>([]);
  const [determinationForm, setDeterminationForm] = useState({
    reference: "", date: "", grantReference: "", approvedBy: "",
    status: "under_review" as EOTDeterminationStatus, remarks: "",
  });
  const [determinationRows, setDeterminationRows] = useState<Record<string, DeterminationRow>>({});
  const [csvTarget, setCsvTarget] = useState<null | { kind: "submission" | "determination"; id: string; label: string }>(null);

  const load = useCallback(async () => {
    if (!projectId) return;
    try {
      const result = await getKeyDateWorkflow(projectId);
      setWorkflow(result);
      onBaselineStatusChange?.(result.baseline_status === "frozen");
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Failed to load Key Date revision history");
    }
  }, [onBaselineStatusChange, projectId]);

  useEffect(() => { void load(); }, [load]);

  const refresh = async () => {
    await Promise.all([load(), Promise.resolve(onChanged())]);
  };

  const openSubmission = (submission?: EOTSubmissionRevisionDTO) => {
    setEditingSubmission(submission ?? null);
    setSubmissionForm(submission ? {
      eotReference: submission.eot_reference || "",
      submissionDate: submission.contractor_submission_date?.slice(0, 10) || "",
      letterReference: submission.contractor_letter_reference || "",
      cutoffDate: submission.claim_cutoff_date?.slice(0, 10) || "",
      remarks: submission.remarks || "",
      status: submission.status === "draft" ? "draft" : "submitted",
    } : { ...blankSubmission });
    const existing = new Map((submission?.items ?? []).map((item) => [item.milestone_ref.toLowerCase(), item]));
    setSubmissionRows(Object.fromEntries(milestones.map((milestone) => {
      const ref = milestone.milestone_ref || "";
      const item = existing.get(ref.toLowerCase());
      return [ref, {
        include: !!item,
        submittedDate: item?.eot_submitted_date?.slice(0, 10) || "",
        claimedDays: item?.claimed_extension_days == null ? "" : String(item.claimed_extension_days),
        remarks: item?.remarks || "",
      }];
    })));
    setSubmissionOpen(true);
  };

  const saveSubmission = async () => {
    const items = milestones.flatMap((milestone) => {
      const ref = milestone.milestone_ref || "";
      const row = submissionRows[ref];
      if (!row?.include) return [];
      if (!row.submittedDate) throw new Error(`Submitted date is required for ${ref}`);
      return [{
        milestone_ref: ref,
        eot_submitted_date: new Date(row.submittedDate).toISOString(),
        claimed_extension_days: row.claimedDays ? Number(row.claimedDays) : undefined,
        remarks: row.remarks || undefined,
      }];
    });
    if (!items.length) {
      toast.error("Select at least one affected milestone");
      return;
    }
    setBusy(true);
    try {
      const payload = {
        eot_reference: submissionForm.eotReference || undefined,
        contractor_submission_date: iso(submissionForm.submissionDate),
        contractor_letter_reference: submissionForm.letterReference || undefined,
        claim_cutoff_date: iso(submissionForm.cutoffDate),
        remarks: submissionForm.remarks || undefined,
        status: submissionForm.status,
        items,
      };
      if (editingSubmission) await updateEOTSubmissionRevision(editingSubmission.id, payload);
      else await createEOTSubmissionRevision({ project_id: projectId, contract_id: "primary", ...payload });
      toast.success(editingSubmission ? `${editingSubmission.revision_label} updated` : "EOT submission revision created");
      setSubmissionOpen(false);
      await refresh();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || "Failed to save EOT submission");
    } finally {
      setBusy(false);
    }
  };

  const coveredItems = useMemo(() => {
    const result = new Map<string, EOTSubmissionRevisionDTO["items"][number]>();
    for (const submission of workflow?.submissions ?? []) {
      if (!selectedSubmissionIds.includes(submission.id)) continue;
      for (const item of submission.items) result.set(item.milestone_ref.toLowerCase(), item);
    }
    return [...result.values()];
  }, [selectedSubmissionIds, workflow]);

  const syncDeterminationRows = (ids: string[]) => {
    const rows: Record<string, DeterminationRow> = { ...determinationRows };
    const refs = new Set<string>();
    for (const submission of workflow?.submissions ?? []) {
      if (!ids.includes(submission.id)) continue;
      for (const item of submission.items) {
        refs.add(item.milestone_ref);
        rows[item.milestone_ref] ||= { grantDate: "", grantedDays: "", result: "pending", remarks: "" };
      }
    }
    for (const ref of Object.keys(rows)) if (!refs.has(ref)) delete rows[ref];
    setDeterminationRows(rows);
  };

  const openDetermination = (submission: EOTSubmissionRevisionDTO) => {
    setEditingDetermination(null);
    const ids = [submission.id];
    setSelectedSubmissionIds(ids);
    setDeterminationForm({ reference: "", date: "", grantReference: "", approvedBy: "", status: "under_review", remarks: "" });
    setDeterminationRows(Object.fromEntries(submission.items.map((item) => [item.milestone_ref, {
      grantDate: "", grantedDays: "", result: "pending" as EOTDeterminationResult, remarks: "",
    }])));
    setDeterminationOpen(true);
  };

  const openExistingDetermination = (determination: EOTDeterminationDTO) => {
    setEditingDetermination(determination);
    setSelectedSubmissionIds(determination.eot_submission_ids);
    setDeterminationForm({
      reference: determination.determination_reference || "",
      date: determination.determination_date?.slice(0, 10) || "",
      grantReference: determination.approval_grant_reference || "",
      approvedBy: determination.approved_by || "",
      status: determination.status,
      remarks: determination.remarks || "",
    });
    setDeterminationRows(Object.fromEntries(determination.items.map((item) => [item.milestone_ref, {
      grantDate: item.eot_granted_date?.slice(0, 10) || "",
      grantedDays: item.granted_extension_days == null ? "" : String(item.granted_extension_days),
      result: item.determination_result,
      remarks: item.remarks || "",
    }])));
    setDeterminationOpen(true);
  };

  const saveDetermination = async () => {
    if (!selectedSubmissionIds.length) return;
    const items = coveredItems.map((item) => {
      const row = determinationRows[item.milestone_ref] || { grantDate: "", grantedDays: "", result: "pending", remarks: "" };
      return {
        milestone_ref: item.milestone_ref,
        eot_granted_date: iso(row.grantDate),
        granted_extension_days: row.grantedDays ? Number(row.grantedDays) : undefined,
        determination_result: row.result,
        remarks: row.remarks || undefined,
      };
    });
    setBusy(true);
    try {
      const payload = {
        project_id: projectId,
        contract_id: "primary",
        eot_submission_ids: selectedSubmissionIds,
        determination_reference: determinationForm.reference || undefined,
        determination_date: iso(determinationForm.date),
        approval_grant_reference: determinationForm.grantReference || undefined,
        approved_by: determinationForm.approvedBy || undefined,
        status: determinationForm.status,
        remarks: determinationForm.remarks || undefined,
        items,
      };
      if (editingDetermination) {
        await updateEOTDetermination(editingDetermination.id, payload);
      } else {
        await createEOTDetermination(payload);
      }
      toast.success(editingDetermination ? "Determination updated" : "Determination created; freeze it separately when contractually confirmed");
      setDeterminationOpen(false);
      await refresh();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Failed to create determination");
    } finally {
      setBusy(false);
    }
  };

  const freezeBaseline = async () => {
    if (!window.confirm("You are about to freeze the Original Contractual Key Date baseline for this project. Once frozen, these dates will become read-only through normal operations. Future claimed or approved changes must be recorded through EOT revisions. Continue?")) return;
    setBusy(true);
    try {
      await freezeOriginalKeyDates(projectId);
      toast.success("Original Key Dates frozen");
      await refresh();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Baseline freeze failed");
    } finally { setBusy(false); }
  };

  const lockSubmission = async (submission: EOTSubmissionRevisionDTO) => {
    if (!window.confirm(`Lock ${submission.revision_label} submission? Submitted dates and references will become read-only.`)) return;
    setBusy(true);
    try {
      await lockEOTSubmissionRevision(submission.id);
      toast.success(`${submission.revision_label} submission locked; its determination remains independent`);
      await refresh();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Submission lock failed");
    } finally { setBusy(false); }
  };

  const freezeDetermination = async (id: string) => {
    if (!window.confirm("Freeze this client determination? Only granted milestone dates will become contractually effective.")) return;
    setBusy(true);
    try {
      await freezeEOTDetermination(id);
      toast.success("Determination frozen and effective granted dates applied");
      await refresh();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Determination freeze failed");
    } finally { setBusy(false); }
  };

  const download = async (
    kind: "baseline" | "history" | "submission" | "determination",
    format: "csv" | "xlsx" | "pdf",
    resourceId?: string,
    label = "key-date-record",
  ) => {
    try {
      const blob = await exportKeyDateWorkflow(kind, format, { projectId, contractId: "primary", resourceId });
      downloadBlob(blob, `${label}.${format}`);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || "Download failed");
    }
  };

  if (!workflow) {
    return <Card><CardContent className="flex items-center py-6 text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" />Loading contractual workflow…</CardContent></Card>;
  }

  const lockedSubmissions = workflow.submissions.filter((submission) => submission.status === "locked");

  return (
    <>
      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <CardTitle>Contractual Baseline &amp; EOT Revisions</CardTitle>
              <CardDescription>Contractor submissions remain separate from frozen client determinations.</CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              {workflow.baseline_status === "draft" ? (
                <Button onClick={freezeBaseline} disabled={busy || milestones.length === 0}><LockKeyhole className="mr-2 h-4 w-4" />Freeze Original Key Dates</Button>
              ) : (
                <>
                  {(["csv", "xlsx", "pdf"] as const).map((format) => (
                    <Button key={`baseline-${format}`} variant="outline" size="sm" onClick={() => void download("baseline", format, undefined, "key-dates-original-frozen")}>
                      <Download className="mr-1 h-4 w-4" />Baseline {format.toUpperCase()}
                    </Button>
                  ))}
                  <Button onClick={() => openSubmission()}><PlusCircle className="mr-2 h-4 w-4" />Create EOT Submission</Button>
                </>
              )}
            </div>
          </div>
          <div className="grid gap-2 pt-3 sm:grid-cols-2 lg:grid-cols-4">
            <MiniStat label="Current Contractual Baseline" value={workflow.current_contractual_baseline} />
            <MiniStat label="Latest EOT Submission" value={workflow.latest_eot_submission || "None"} />
            <MiniStat label="Pending Determinations" value={workflow.pending_determinations} />
            <MiniStat label="Oldest Pending" value={workflow.oldest_pending_submission || "None"} />
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2"><History className="h-4 w-4" /><span className="font-medium">Revision History</span></div>
            {workflow.baseline_status === "frozen" && (
              <div className="flex gap-1">
                {(["csv", "xlsx", "pdf"] as const).map((format) => <Button key={format} size="sm" variant="outline" onClick={() => void download("history", format, undefined, "key-date-eot-history")}>{format.toUpperCase()}</Button>)}
              </div>
            )}
          </div>
          <Table>
            <TableHeader><TableRow><TableHead>Revision</TableHead><TableHead>Submission</TableHead><TableHead>Determination</TableHead><TableHead>Submitted On</TableHead><TableHead>Affected</TableHead><TableHead className="text-right">Actions</TableHead></TableRow></TableHeader>
            <TableBody>
              <TableRow><TableCell className="font-medium">Original</TableCell><TableCell><Badge variant="outline">{workflow.baseline_status}</Badge></TableCell><TableCell>—</TableCell><TableCell>—</TableCell><TableCell>{milestones.length}</TableCell><TableCell className="text-right">{workflow.baseline_status === "frozen" ? <Button size="sm" variant="ghost" onClick={() => void download("baseline", "csv", undefined, "key-dates-original-frozen")}>Download</Button> : "—"}</TableCell></TableRow>
              {workflow.submissions.map((submission) => {
                const determinations = workflow.determinations.filter((determination) => determination.eot_submission_ids.includes(submission.id));
                return <TableRow key={submission.id}>
                  <TableCell className="font-medium">{submission.revision_label}</TableCell>
                  <TableCell><Badge variant="outline">{submission.status}</Badge></TableCell>
                  <TableCell className="text-xs">{determinations.length ? determinations.map((d) => `${d.status}${d.frozen_at ? " / frozen" : ""}`).join(", ") : "Pending"}</TableCell>
                  <TableCell>{fmt(submission.contractor_submission_date)}</TableCell>
                  <TableCell>{submission.items.length}</TableCell>
                  <TableCell><div className="flex flex-wrap justify-end gap-1">
                    {!submission.locked_at && <Button size="sm" variant="outline" onClick={() => openSubmission(submission)}>Edit</Button>}
                    {!submission.locked_at && <Button size="sm" variant="outline" onClick={() => setCsvTarget({ kind: "submission", id: submission.id, label: submission.revision_label })}><Upload className="mr-1 h-3 w-3" />CSV</Button>}
                    {!submission.locked_at && <Button size="sm" onClick={() => void lockSubmission(submission)}>Lock</Button>}
                    {submission.locked_at && <Button size="sm" variant="outline" onClick={() => openDetermination(submission)}>Determine</Button>}
                    {(["csv", "xlsx", "pdf"] as const).map((format) => <Button key={`${submission.id}-${format}`} size="sm" variant="ghost" onClick={() => void download("submission", format, submission.id, `${submission.revision_label.toLowerCase()}-submission`)}>{format.toUpperCase()}</Button>)}
                  </div></TableCell>
                </TableRow>;
              })}
              {workflow.determinations.map((determination) => <TableRow key={`det-${determination.id}`} className="bg-muted/20">
                <TableCell className="text-xs">↳ {determination.covered_revision_labels.join(" + ")}</TableCell>
                <TableCell>Determination</TableCell>
                <TableCell><Badge variant="outline">{determination.status}{determination.frozen_at ? " / frozen" : ""}</Badge></TableCell>
                <TableCell>{fmt(determination.determination_date)}</TableCell><TableCell>{determination.items.length}</TableCell>
                <TableCell><div className="flex justify-end gap-1">
                  {!determination.frozen_at && <Button size="sm" variant="outline" onClick={() => setCsvTarget({ kind: "determination", id: determination.id, label: "EOT determination" })}><Upload className="mr-1 h-3 w-3" />CSV</Button>}
                  {!determination.frozen_at && <Button size="sm" variant="outline" onClick={() => openExistingDetermination(determination)}>Edit</Button>}
                  {!determination.frozen_at && <Button size="sm" onClick={() => void freezeDetermination(determination.id)}><FileCheck2 className="mr-1 h-3 w-3" />Freeze</Button>}
                  {(["csv", "xlsx", "pdf"] as const).map((format) => <Button key={`${determination.id}-${format}`} size="sm" variant="ghost" onClick={() => void download("determination", format, determination.id, "eot-determination")}>{format.toUpperCase()}</Button>)}
                </div></TableCell>
              </TableRow>)}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Dialog open={submissionOpen} onOpenChange={setSubmissionOpen}>
        <DialogContent className="max-h-[90vh] max-w-5xl overflow-y-auto">
          <DialogHeader><DialogTitle>{editingSubmission ? `Edit ${editingSubmission.revision_label}` : "Create EOT Submission"}</DialogTitle><DialogDescription>The contractual date shown for each item is snapshotted by the server when this revision is saved.</DialogDescription></DialogHeader>
          <div className="grid gap-3 md:grid-cols-3">
            <div><Label>EOT reference</Label><Input value={submissionForm.eotReference} onChange={(e) => setSubmissionForm({ ...submissionForm, eotReference: e.target.value })} /></div>
            <div><Label>Contractor submission date</Label><Input type="date" value={submissionForm.submissionDate} onChange={(e) => setSubmissionForm({ ...submissionForm, submissionDate: e.target.value })} /></div>
            <div><Label>Contractor letter reference</Label><Input value={submissionForm.letterReference} onChange={(e) => setSubmissionForm({ ...submissionForm, letterReference: e.target.value })} /></div>
            <div><Label>Claim cut-off date</Label><Input type="date" value={submissionForm.cutoffDate} onChange={(e) => setSubmissionForm({ ...submissionForm, cutoffDate: e.target.value })} /></div>
            <div><Label>Status before lock</Label><Select value={submissionForm.status} onValueChange={(value: "draft" | "submitted") => setSubmissionForm({ ...submissionForm, status: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent><SelectItem value="draft">Draft</SelectItem><SelectItem value="submitted">Submitted</SelectItem></SelectContent></Select></div>
            <div><Label>Remarks</Label><Input value={submissionForm.remarks} onChange={(e) => setSubmissionForm({ ...submissionForm, remarks: e.target.value })} /></div>
          </div>
          <Table><TableHeader><TableRow><TableHead className="w-10">Use</TableHead><TableHead>Ref / Description</TableHead><TableHead>Current Contractual</TableHead><TableHead>EOT Submitted</TableHead><TableHead>Claimed Days</TableHead><TableHead>Remarks</TableHead></TableRow></TableHeader><TableBody>
            {milestones.map((milestone) => {
              const ref = milestone.milestone_ref || "";
              const row = submissionRows[ref] || { include: false, submittedDate: "", claimedDays: "", remarks: "" };
              const change = (next: Partial<SubmissionRow>) => setSubmissionRows({ ...submissionRows, [ref]: { ...row, ...next } });
              return <TableRow key={milestone.id}><TableCell><Checkbox checked={row.include} onCheckedChange={(value) => change({ include: !!value })} disabled={!ref} /></TableCell><TableCell><div className="font-mono text-xs">{ref || "Missing ref"}</div><div className="max-w-[220px] truncate text-xs text-muted-foreground">{milestone.title}</div></TableCell><TableCell>{fmt(milestone.current_approved_key_date)}</TableCell><TableCell><Input type="date" value={row.submittedDate} disabled={!row.include} onChange={(e) => change({ submittedDate: e.target.value })} /></TableCell><TableCell><Input type="number" min={0} value={row.claimedDays} disabled={!row.include} onChange={(e) => change({ claimedDays: e.target.value })} /></TableCell><TableCell><Input value={row.remarks} disabled={!row.include} onChange={(e) => change({ remarks: e.target.value })} /></TableCell></TableRow>;
            })}
          </TableBody></Table>
          <DialogFooter><Button variant="outline" onClick={() => setSubmissionOpen(false)} disabled={busy}>Cancel</Button><Button onClick={() => void saveSubmission()} disabled={busy}>{busy && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}Save Submission</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={determinationOpen} onOpenChange={setDeterminationOpen}>
        <DialogContent className="max-h-[90vh] max-w-5xl overflow-y-auto">
          <DialogHeader><DialogTitle>{editingDetermination ? "Edit Client / Engineer Determination" : "Create Client / Engineer Determination"}</DialogTitle><DialogDescription>A determination can cover multiple locked EOT submissions. It remains non-contractual until separately frozen.</DialogDescription></DialogHeader>
          <div><Label>Covers locked submissions</Label><div className="mt-2 flex flex-wrap gap-3">{lockedSubmissions.map((submission) => <label key={submission.id} className="flex items-center gap-2 rounded border px-3 py-2 text-sm"><Checkbox checked={selectedSubmissionIds.includes(submission.id)} disabled={!!editingDetermination} onCheckedChange={(checked) => { const ids = checked ? [...selectedSubmissionIds, submission.id] : selectedSubmissionIds.filter((id) => id !== submission.id); setSelectedSubmissionIds(ids); syncDeterminationRows(ids); }} />{submission.revision_label}</label>)}</div></div>
          <div className="grid gap-3 md:grid-cols-3">
            <div><Label>Determination reference</Label><Input value={determinationForm.reference} onChange={(e) => setDeterminationForm({ ...determinationForm, reference: e.target.value })} /></div>
            <div><Label>Determination date</Label><Input type="date" value={determinationForm.date} onChange={(e) => setDeterminationForm({ ...determinationForm, date: e.target.value })} /></div>
            <div><Label>Approval / grant reference</Label><Input value={determinationForm.grantReference} onChange={(e) => setDeterminationForm({ ...determinationForm, grantReference: e.target.value })} /></div>
            <div><Label>Approved by</Label><Input value={determinationForm.approvedBy} onChange={(e) => setDeterminationForm({ ...determinationForm, approvedBy: e.target.value })} /></div>
            <div><Label>Determination status</Label><Select value={determinationForm.status} onValueChange={(value: EOTDeterminationStatus) => setDeterminationForm({ ...determinationForm, status: value })}><SelectTrigger><SelectValue /></SelectTrigger><SelectContent>{["under_review", "pending", "granted", "partially_granted", "rejected", "no_extension"] .map((value) => <SelectItem key={value} value={value}>{value.replaceAll("_", " ")}</SelectItem>)}</SelectContent></Select></div>
            <div><Label>Remarks</Label><Input value={determinationForm.remarks} onChange={(e) => setDeterminationForm({ ...determinationForm, remarks: e.target.value })} /></div>
          </div>
          <Table><TableHeader><TableRow><TableHead>Milestone</TableHead><TableHead>Submitted</TableHead><TableHead>Result</TableHead><TableHead>Granted Date</TableHead><TableHead>Granted Days</TableHead><TableHead>Remarks</TableHead></TableRow></TableHeader><TableBody>{coveredItems.map((item) => {
            const row = determinationRows[item.milestone_ref] || { grantDate: "", grantedDays: "", result: "pending", remarks: "" };
            const change = (next: Partial<DeterminationRow>) => setDeterminationRows({ ...determinationRows, [item.milestone_ref]: { ...row, ...next } });
            return <TableRow key={item.milestone_ref}><TableCell className="font-mono text-xs">{item.milestone_ref}</TableCell><TableCell>{fmt(item.eot_submitted_date)}</TableCell><TableCell><Select value={row.result} onValueChange={(value: EOTDeterminationResult) => change({ result: value })}><SelectTrigger className="w-40"><SelectValue /></SelectTrigger><SelectContent>{["pending", "granted", "partially_granted", "rejected", "no_change"].map((value) => <SelectItem key={value} value={value}>{value.replaceAll("_", " ")}</SelectItem>)}</SelectContent></Select></TableCell><TableCell><Input type="date" value={row.grantDate} onChange={(e) => change({ grantDate: e.target.value })} /></TableCell><TableCell><Input type="number" min={0} value={row.grantedDays} onChange={(e) => change({ grantedDays: e.target.value })} /></TableCell><TableCell><Input value={row.remarks} onChange={(e) => change({ remarks: e.target.value })} /></TableCell></TableRow>;
          })}</TableBody></Table>
          <DialogFooter><Button variant="outline" onClick={() => setDeterminationOpen(false)} disabled={busy}>Cancel</Button><Button onClick={() => void saveDetermination()} disabled={busy || selectedSubmissionIds.length === 0}>Save Determination</Button></DialogFooter>
        </DialogContent>
      </Dialog>

      <CsvImportDialog
        open={csvTarget !== null}
        onOpenChange={(open) => !open && setCsvTarget(null)}
        title={`Upload ${csvTarget?.label || "EOT"} CSV`}
        description={csvTarget?.kind === "determination" ? "Only this determination's grant/result fields are editable." : "Only this EOT revision's submitted date, claimed days, and remarks are editable."}
        sampleFileName={`${(csvTarget?.label || "eot").toLowerCase().replaceAll(" ", "-")}-template.csv`}
        onDownloadTemplate={() => csvTarget?.kind === "determination" ? downloadEOTDeterminationTemplate(csvTarget.id) : downloadEOTSubmissionTemplate(csvTarget!.id)}
        onPreview={(file) => csvTarget?.kind === "determination" ? previewEOTDeterminationCsv(csvTarget.id, file) : previewEOTSubmissionCsv(csvTarget!.id, file)}
        onImport={(file) => csvTarget?.kind === "determination" ? importEOTDeterminationCsv(csvTarget.id, file) : importEOTSubmissionCsv(csvTarget!.id, file)}
        onImported={refresh}
        rowLabel={(row) => String(row.data?.milestone_ref || `Row ${row.row_number}`)}
      />
    </>
  );
};

export default KeyDateRevisionWorkflow;
