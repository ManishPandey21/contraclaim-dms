import React, { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { ArrowLeft, CheckCircle2, FilePlus2, History, Loader2 } from "lucide-react";
import { toast } from "sonner";
import {
  EOTDTO,
  ExtensionHistoryDTO,
  getExtensionHistory,
  getKeyDateWorkflow,
  getMilestone,
  listEOTs,
  MilestoneDTO,
  KeyDateWorkflowSummaryDTO,
  recordAchievement,
  reviewEOT,
  submitEOT,
} from "@/services/key-dates-api";
import { statusColor, statusLabel, achievementText } from "@/lib/key-date-helpers";

const fmt = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");
const toISO = (d: string) => (d ? new Date(d).toISOString() : undefined);

const Field: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div>
    <p className="text-xs uppercase text-muted-foreground">{label}</p>
    <p className="text-sm font-medium">{value}</p>
  </div>
);

const KeyDateDetailPage: React.FC = () => {
  const { id = "" } = useParams<{ id: string }>();
  const [m, setM] = useState<MilestoneDTO | null>(null);
  const [eots, setEots] = useState<EOTDTO[]>([]);
  const [history, setHistory] = useState<ExtensionHistoryDTO[]>([]);
  const [workflow, setWorkflow] = useState<KeyDateWorkflowSummaryDTO | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);

  const [eotOpen, setEotOpen] = useState(false);
  const [eotForm, setEotForm] = useState({ requested_extension_days: "", eot_letter_reference: "", requested_revised_key_date: "", reason: "" });

  const [reviewEot, setReviewEot] = useState<EOTDTO | null>(null);
  const [reviewForm, setReviewForm] = useState({ approved_extension_days: "", approved_revised_key_date: "", approval_letter_reference: "", approving_authority: "", approval_remarks: "" });

  const [achOpen, setAchOpen] = useState(false);
  const [achForm, setAchForm] = useState({ actual_achievement_date: "", achievement_remarks: "", client_notification_required: false, client_notification_ref: "", client_notification_date: "" });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const mi = await getMilestone(id);
      const [es, hs, wf] = await Promise.all([
        listEOTs(id),
        getExtensionHistory(id),
        mi.project_id ? getKeyDateWorkflow(mi.project_id) : Promise.resolve(null),
      ]);
      setM(mi);
      setEots(es);
      setHistory(hs);
      setWorkflow(wf);
    } catch {
      toast.error("Failed to load milestone");
    } finally {
      setLoading(false);
    }
  }, [id]);

  useEffect(() => { void load(); }, [load]);

  const onSubmitEot = async () => {
    if (!eotForm.requested_extension_days || !eotForm.eot_letter_reference.trim()) {
      toast.error("Extension days and EOT letter reference are required");
      return;
    }
    setBusy(true);
    try {
      await submitEOT(id, {
        requested_extension_days: Number(eotForm.requested_extension_days),
        eot_letter_reference: eotForm.eot_letter_reference.trim(),
        requested_revised_key_date: toISO(eotForm.requested_revised_key_date),
        reason: eotForm.reason || undefined,
        submit: true,
      });
      toast.success("EOT submitted");
      setEotOpen(false);
      setEotForm({ requested_extension_days: "", eot_letter_reference: "", requested_revised_key_date: "", reason: "" });
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to submit EOT");
    } finally {
      setBusy(false);
    }
  };

  const decide = async (eot: EOTDTO, decision: "approved" | "rejected" | "under_review" | "withdrawn") => {
    if (decision === "approved" && (!reviewForm.approval_letter_reference.trim() || !reviewForm.approved_revised_key_date)) {
      toast.error("Approval letter reference and approved revised key date are required");
      return;
    }
    setBusy(true);
    try {
      await reviewEOT(id, eot.id, {
        decision,
        approved_extension_days: reviewForm.approved_extension_days ? Number(reviewForm.approved_extension_days) : undefined,
        approved_revised_key_date: toISO(reviewForm.approved_revised_key_date),
        approval_letter_reference: reviewForm.approval_letter_reference || undefined,
        approving_authority: reviewForm.approving_authority || undefined,
        approval_remarks: reviewForm.approval_remarks || undefined,
      });
      toast.success(`EOT ${decision}`);
      setReviewEot(null);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to review EOT");
    } finally {
      setBusy(false);
    }
  };

  const onAchieve = async () => {
    if (!achForm.actual_achievement_date) {
      toast.error("Actual achievement date is required");
      return;
    }
    if (achForm.client_notification_required && !achForm.client_notification_ref.trim()) {
      toast.error("Client notification reference is required");
      return;
    }
    setBusy(true);
    try {
      await recordAchievement(id, {
        actual_achievement_date: new Date(achForm.actual_achievement_date).toISOString(),
        achievement_remarks: achForm.achievement_remarks || undefined,
        client_notification_required: achForm.client_notification_required,
        client_notification_ref: achForm.client_notification_ref || undefined,
        client_notification_date: toISO(achForm.client_notification_date),
      });
      toast.success("Achievement recorded");
      setAchOpen(false);
      await load();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to record achievement");
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading…
      </div>
    );
  }
  if (!m) {
    return (
      <div className="container mx-auto p-6">
        <p className="text-muted-foreground">Milestone not found.</p>
        <Button asChild variant="link" className="px-0"><Link to="/key-dates">Back to register</Link></Button>
      </div>
    );
  }

  const achieved = !!m.actual_achievement_date;

  return (
    <div className="container mx-auto space-y-6 p-6">
      <h1 className="text-2xl font-bold">Key Date Details</h1>
      <div className="flex items-center justify-between">
        <Button asChild variant="ghost" size="sm">
          <Link to="/key-dates"><ArrowLeft className="mr-2 h-4 w-4" />Register</Link>
        </Button>
        <div className="flex gap-2">
          {!achieved && workflow?.baseline_status !== "frozen" && (
            <Button variant="outline" size="sm" onClick={() => setEotOpen(true)}>
              <FilePlus2 className="mr-2 h-4 w-4" />Submit EOT
            </Button>
          )}
          {!achieved && workflow?.baseline_status === "frozen" && (
            <Button asChild variant="outline" size="sm">
              <Link to="/key-dates"><FilePlus2 className="mr-2 h-4 w-4" />Create project EOT submission</Link>
            </Button>
          )}
          {!achieved && (
            <Button size="sm" onClick={() => setAchOpen(true)}>
              <CheckCircle2 className="mr-2 h-4 w-4" />Record achievement
            </Button>
          )}
        </div>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>{m.title}</CardTitle>
            <Badge className={statusColor(m.status)}>{statusLabel(m.status)}</Badge>
            {m.eot_status && <Badge variant="outline">EOT: {m.eot_status}</Badge>}
          </div>
          {m.milestone_ref && <CardDescription className="font-mono text-xs">{m.milestone_ref}</CardDescription>}
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <Field label="Contractual week" value={m.contractual_week_number} />
          <Field label="Original key date" value={fmt(m.original_planned_key_date)} />
          <Field label="Calculated key date" value={fmt(m.calculated_key_date)} />
          <Field label="Current approved key date" value={fmt(m.current_approved_key_date)} />
          <Field label="Days remaining" value={m.days_remaining ?? "—"} />
          <Field label="Responsible party" value={m.responsible_party_id || "—"} />
          <Field label="Achievement" value={achievementText(m)} />
          <Field label="Actual date" value={fmt(m.actual_achievement_date)} />
          <Field label="Revisions" value={m.current_revision} />
        </CardContent>
      </Card>

      {workflow?.baseline_status === "frozen" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Contractual lineage</CardTitle>
            <CardDescription>
              Pending contractor submissions do not change the Current Contractual Date.
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            <div className="rounded-md border p-3 text-sm">
              <p className="font-medium">Original Contractual Date</p>
              <p>{fmt(m.original_planned_key_date)}</p>
            </div>
            {workflow.submissions.flatMap((submission) => {
              const item = submission.items.find((entry) => entry.key_date_id === m.id || entry.milestone_ref === m.milestone_ref);
              if (!item) return [];
              const determinations = workflow.determinations.flatMap((determination) => {
                if (!determination.eot_submission_ids.includes(submission.id)) return [];
                const determined = determination.items.find((entry) => entry.key_date_id === m.id || entry.milestone_ref === m.milestone_ref);
                return determined ? [{ determination, determined }] : [];
              });
              return [(
                <div key={submission.id} className="rounded-md border p-3 text-sm">
                  <div className="flex items-center justify-between gap-2">
                    <p className="font-medium">{submission.revision_label}</p>
                    <Badge variant="outline">{submission.status}</Badge>
                  </div>
                  <p>Submitted: {fmt(item.eot_submitted_date)} · Base at submission: {fmt(item.contractual_date_at_submission)}</p>
                  {determinations.length === 0 ? (
                    <p className="text-muted-foreground">Determination: Pending</p>
                  ) : determinations.map(({ determination, determined }) => (
                    <p key={determination.id} className="text-muted-foreground">
                      Determination: {determined.determination_result} · Granted: {fmt(determined.eot_granted_date)}
                      {determination.frozen_at ? " · Frozen" : " · Not frozen"}
                    </p>
                  ))}
                </div>
              )];
            })}
            <div className="rounded-md border border-blue-200 bg-blue-50 p-3 text-sm dark:bg-blue-950/20">
              <p className="font-medium">Current Contractual Date</p>
              <p>{fmt(m.current_approved_key_date)}</p>
            </div>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">EOT applications</CardTitle>
            <CardDescription>Extension-of-time requests against this milestone.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {eots.length === 0 ? (
              <p className="text-sm text-muted-foreground">No EOT applications.</p>
            ) : (
              eots.map((e) => (
                <div key={e.id} className="rounded-md border p-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">{e.eot_letter_reference || "Draft"}</span>
                    <Badge variant="outline">{e.status}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    +{e.requested_extension_days ?? "?"}d · revised {fmt(e.requested_revised_key_date)}
                  </p>
                  {["submitted", "under_review"].includes(e.status) && (
                    <Button
                      size="sm"
                      variant="outline"
                      className="mt-2"
                      onClick={() => {
                        setReviewEot(e);
                        setReviewForm({
                          approved_extension_days: String(e.requested_extension_days ?? ""),
                          approved_revised_key_date: e.requested_revised_key_date ? e.requested_revised_key_date.slice(0, 10) : "",
                          approval_letter_reference: "", approving_authority: "", approval_remarks: "",
                        });
                      }}
                    >
                      Review
                    </Button>
                  )}
                </div>
              ))
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2 text-base">
              <History className="h-4 w-4" />Extension history
            </CardTitle>
            <CardDescription>Every revision is preserved; the original date is never overwritten.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {history.length === 0 ? (
              <p className="text-sm text-muted-foreground">No revisions yet.</p>
            ) : (
              history.map((h) => (
                <div key={h.id} className="rounded-md border p-2 text-sm">
                  <div className="flex items-center justify-between">
                    <span className="font-medium">Revision {h.revision_number}</span>
                    <Badge variant={h.status === "approved" ? "default" : "destructive"}>{h.status}</Badge>
                  </div>
                  <p className="text-xs text-muted-foreground">
                    {fmt(h.previous_key_date)} → {fmt(h.approved_revised_key_date || h.requested_revised_key_date)}
                    {h.approval_letter_reference ? ` · ${h.approval_letter_reference}` : ""}
                  </p>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      {/* Submit EOT */}
      <Dialog open={eotOpen} onOpenChange={setEotOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Submit EOT application</DialogTitle>
            <DialogDescription>The EOT letter reference is mandatory on submission.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Extension days</Label>
                <Input type="number" min={1} value={eotForm.requested_extension_days} onChange={(e) => setEotForm({ ...eotForm, requested_extension_days: e.target.value })} />
              </div>
              <div>
                <Label>Requested revised date</Label>
                <Input type="date" value={eotForm.requested_revised_key_date} onChange={(e) => setEotForm({ ...eotForm, requested_revised_key_date: e.target.value })} />
              </div>
            </div>
            <div>
              <Label>EOT letter reference</Label>
              <Input value={eotForm.eot_letter_reference} onChange={(e) => setEotForm({ ...eotForm, eot_letter_reference: e.target.value })} placeholder="EOT/2026/001" />
            </div>
            <div>
              <Label>Reason</Label>
              <Textarea value={eotForm.reason} onChange={(e) => setEotForm({ ...eotForm, reason: e.target.value })} rows={2} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setEotOpen(false)} disabled={busy}>Cancel</Button>
            <Button onClick={onSubmitEot} disabled={busy}>Submit</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Review EOT */}
      <Dialog open={reviewEot !== null} onOpenChange={(o) => !o && setReviewEot(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Review EOT {reviewEot?.eot_letter_reference}</DialogTitle>
            <DialogDescription>Approval letter reference and revised date are required to approve.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-3">
              <div>
                <Label>Approved days</Label>
                <Input type="number" min={0} value={reviewForm.approved_extension_days} onChange={(e) => setReviewForm({ ...reviewForm, approved_extension_days: e.target.value })} />
              </div>
              <div>
                <Label>Approved revised date</Label>
                <Input type="date" value={reviewForm.approved_revised_key_date} onChange={(e) => setReviewForm({ ...reviewForm, approved_revised_key_date: e.target.value })} />
              </div>
            </div>
            <div>
              <Label>Approval letter reference</Label>
              <Input value={reviewForm.approval_letter_reference} onChange={(e) => setReviewForm({ ...reviewForm, approval_letter_reference: e.target.value })} placeholder="APP/2026/001" />
            </div>
            <div>
              <Label>Approving authority</Label>
              <Input value={reviewForm.approving_authority} onChange={(e) => setReviewForm({ ...reviewForm, approving_authority: e.target.value })} />
            </div>
            <div>
              <Label>Remarks</Label>
              <Textarea value={reviewForm.approval_remarks} onChange={(e) => setReviewForm({ ...reviewForm, approval_remarks: e.target.value })} rows={2} />
            </div>
          </div>
          <DialogFooter className="flex-col gap-2 sm:flex-row">
            <Button variant="outline" onClick={() => reviewEot && decide(reviewEot, "under_review")} disabled={busy}>Mark under review</Button>
            <Button variant="outline" className="text-destructive" onClick={() => reviewEot && decide(reviewEot, "rejected")} disabled={busy}>Reject</Button>
            <Button onClick={() => reviewEot && decide(reviewEot, "approved")} disabled={busy}>Approve</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Record achievement */}
      <Dialog open={achOpen} onOpenChange={setAchOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Record actual achievement</DialogTitle>
            <DialogDescription>Delay / early completion is computed against the current approved key date.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div>
              <Label>Actual achievement date</Label>
              <Input type="date" value={achForm.actual_achievement_date} onChange={(e) => setAchForm({ ...achForm, actual_achievement_date: e.target.value })} />
            </div>
            <div>
              <Label>Remarks</Label>
              <Textarea value={achForm.achievement_remarks} onChange={(e) => setAchForm({ ...achForm, achievement_remarks: e.target.value })} rows={2} />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox
                checked={achForm.client_notification_required}
                onCheckedChange={(v) => setAchForm({ ...achForm, client_notification_required: !!v })}
              />
              Client notification required
            </label>
            {achForm.client_notification_required && (
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <Label>Notification reference</Label>
                  <Input value={achForm.client_notification_ref} onChange={(e) => setAchForm({ ...achForm, client_notification_ref: e.target.value })} />
                </div>
                <div>
                  <Label>Notification date</Label>
                  <Input type="date" value={achForm.client_notification_date} onChange={(e) => setAchForm({ ...achForm, client_notification_date: e.target.value })} />
                </div>
              </div>
            )}
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setAchOpen(false)} disabled={busy}>Cancel</Button>
            <Button onClick={onAchieve} disabled={busy}>Record</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default KeyDateDetailPage;
