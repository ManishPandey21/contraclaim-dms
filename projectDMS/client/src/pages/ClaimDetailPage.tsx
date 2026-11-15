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
import {
  AlertTriangle,
  ArrowLeft,
  ClipboardList,
  Clock,
  Download,
  FileText,
  Loader2,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import {
  ClaimDTO,
  downloadEvidenceBundle,
  getClaim,
} from "@/services/claims-api";
import { getTasks, TaskDTO } from "@/services/tasks-api";
import ClaimApprovalDialog from "@/components/claims/ClaimApprovalDialog";
import ClaimAssessmentDialog from "@/components/claims/ClaimAssessmentDialog";
import ClaimTaskDialog from "@/components/claims/ClaimTaskDialog";
import ClaimDocumentLinks from "@/components/claims/ClaimDocumentLinks";
import useHasPermission from "@/hooks/useHasPermission";
import { scopeRefusalMessage } from "@/services/active-scope";
import { describeNotice, type NoticeTone } from "@/lib/claims-helpers";
import ClaimStatusPill from "@/components/claims/ClaimStatusPill";
import { formatCrore, formatDateIN, formatINR } from "@/lib/format-in";

const TYPE_LABELS: Record<string, string> = {
  eot: "Extension of Time",
  variation: "Variation",
  payment_ipc: "Payment / IPC",
  loss_expense: "Loss & Expense",
  acceleration: "Acceleration",
  defect: "Defect",
  other: "Other",
};

const NOTICE_BANNER: Record<NoticeTone, string> = {
  neutral: "border-slate-300 bg-slate-50 text-slate-800",
  ok: "border-green-700 bg-green-50 text-green-900",
  warn: "border-amber-600 bg-amber-50 text-amber-900",
  danger: "border-red-700 bg-red-50 text-red-900",
};

const Field: React.FC<{ label: string; value: React.ReactNode }> = ({ label, value }) => (
  <div>
    <p className="text-xs uppercase text-muted-foreground">{label}</p>
    <p className="text-sm font-medium">{value}</p>
  </div>
);

const ClaimDetailPage: React.FC = () => {
  const { id = "" } = useParams<{ id: string }>();
  const [claim, setClaim] = useState<ClaimDTO | null>(null);
  const [tasks, setTasks] = useState<TaskDTO[]>([]);
  const [loading, setLoading] = useState(true);
  const [approvalOpen, setApprovalOpen] = useState(false);
  const [assessOpen, setAssessOpen] = useState(false);
  const [taskOpen, setTaskOpen] = useState(false);
  const canManageDocumentLinks = useHasPermission("dms.claim.edit");

  const loadTasks = useCallback(async () => {
    try {
      setTasks(await getTasks({ linked_claim_id: id }));
    } catch {
      /* tasks optional */
    }
  }, [id]);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setClaim(await getClaim(id));
      await loadTasks();
    } catch (error) {
      toast.error(scopeRefusalMessage(error, "claim") || "Failed to load claim");
    } finally {
      setLoading(false);
    }
  }, [id, loadTasks]);

  useEffect(() => {
    void load();
  }, [load]);

  const onExport = async () => {
    try {
      await downloadEvidenceBundle(id);
      toast.success("Evidence bundle downloaded");
    } catch (error) {
      toast.error(scopeRefusalMessage(error, "claim") || "Failed to export evidence bundle");
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20 text-muted-foreground">
        <Loader2 className="mr-2 h-5 w-5 animate-spin" />
        Loading claim…
      </div>
    );
  }

  const notice = describeNotice(claim?.notice_assessment);

  if (!claim) {
    return (
      <div className="container mx-auto p-6">
        <p className="text-muted-foreground">Claim not found.</p>
        <Button asChild variant="link" className="px-0">
          <Link to="/claims">Back to register</Link>
        </Button>
      </div>
    );
  }

  return (
    <div className="container mx-auto space-y-6 p-6">
      <h1 className="text-2xl font-bold">Claim Details</h1>
      <div className="flex items-center justify-between">
        <Button asChild variant="ghost" size="sm">
          <Link to="/claims">
            <ArrowLeft className="mr-2 h-4 w-4" />
            Register
          </Link>
        </Button>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" size="sm" onClick={() => setTaskOpen(true)}>
            <ClipboardList className="mr-2 h-4 w-4" />
            New task
          </Button>
          <Button variant="outline" size="sm" onClick={() => setAssessOpen(true)}>
            <Sparkles className="mr-2 h-4 w-4 text-indigo-500" />
            AI assessment
          </Button>
          <Button variant="outline" size="sm" onClick={() => setApprovalOpen(true)}>
            <ShieldCheck className="mr-2 h-4 w-4" />
            Approval
          </Button>
          <Button variant="outline" size="sm" onClick={onExport}>
            <Download className="mr-2 h-4 w-4" />
            Evidence bundle
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center gap-2">
            <CardTitle>{claim.title}</CardTitle>
            <ClaimStatusPill status={claim.status} />
            <Badge variant="outline">{TYPE_LABELS[claim.type] || claim.type}</Badge>
          </div>
          {claim.claim_ref && (
            <CardDescription className="font-mono text-xs">{claim.claim_ref}</CardDescription>
          )}
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <Field
            label="Amount claimed"
            value={
              <>
                {formatINR(claim.amount_claimed, claim.currency)}
                {claim.amount_claimed != null && (
                  <span className="ml-2 text-xs font-normal text-muted-foreground">
                    {formatCrore(claim.amount_claimed, claim.currency)}
                  </span>
                )}
              </>
            }
          />
          <Field label="Amount agreed" value={formatINR(claim.amount_agreed, claim.currency)} />
          <Field label="Response due" value={formatDateIN(claim.response_due_date)} />
          <Field label="Event date" value={formatDateIN(claim.event_date)} />
          <Field label="Became aware on" value={formatDateIN(claim.awareness_date)} />
          <Field label="Notice served on" value={formatDateIN(claim.notice_date)} />
          <Field label="EOT days claimed" value={claim.eot_days_claimed ?? "—"} />
          <Field label="EOT days granted" value={claim.eot_days_granted ?? "—"} />
          <Field
            label="Clauses relied on"
            value={claim.contract_clauses.length > 0 ? claim.contract_clauses.join(", ") : "—"}
          />
        </CardContent>
      </Card>

      {notice ? (
        <div
          role={notice.tone === "danger" ? "alert" : "status"}
          className={`flex items-start gap-3 rounded-md border p-4 text-sm ${NOTICE_BANNER[notice.tone]}`}
        >
          {notice.tone === "danger" || notice.tone === "warn" ? (
            <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
          ) : (
            <Clock className="mt-0.5 h-5 w-5 shrink-0" aria-hidden />
          )}
          <div>
            <p className="font-semibold">{notice.label}</p>
            <p>{notice.sentence}</p>
            {claim.notice_assessment && (
              <p className="pt-1 text-xs opacity-80">
                Counted from {formatDateIN(claim.notice_assessment.basis_date)}; period ends{" "}
                {formatDateIN(claim.notice_assessment.due_date)}.
              </p>
            )}
          </div>
        </div>
      ) : (
        <div role="status" className={`rounded-md border p-4 text-sm ${NOTICE_BANNER.neutral}`}>
          <p className="font-semibold">Notice period not tracked</p>
          <p>
            This claim has no event or awareness date, so its notice period cannot be counted. Add the
            dates from the register (More actions, Edit details).
          </p>
        </div>
      )}

      {claim.description && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Description</CardTitle>
          </CardHeader>
          <CardContent>
            <p className="whitespace-pre-wrap text-sm text-muted-foreground">{claim.description}</p>
          </CardContent>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Linked correspondence</CardTitle>
            <CardDescription>Documents and letters attached to this claim.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            <ClaimDocumentLinks
              claimId={id}
              organizationId={claim.organization_id}
              projectId={claim.project_id}
              frozen={Boolean(claim.evidence_frozen_at)}
              canManage={canManageDocumentLinks}
            />
            {claim.linked_letter_ids.map((letterId) => (
              <Link
                key={letterId}
                to={`/letters/${letterId}/input`}
                className="flex items-center gap-2 rounded-md border p-2 text-sm hover:bg-muted/40"
              >
                <FileText className="h-4 w-4" /> Letter {letterId}
              </Link>
            ))}
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <div className="flex items-center justify-between">
              <CardTitle className="text-base">Tasks ({tasks.length})</CardTitle>
              <Button variant="ghost" size="sm" onClick={() => setTaskOpen(true)}>
                <ClipboardList className="mr-2 h-4 w-4" />
                Add
              </Button>
            </div>
            <CardDescription>Follow-ups tracked against this claim.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {tasks.length === 0 ? (
              <p className="text-sm text-muted-foreground">No tasks yet.</p>
            ) : (
              tasks.map((t) => (
                <div key={t.id} className="flex items-center justify-between rounded-md border p-2 text-sm">
                  <span>{t.title}</span>
                  <Badge variant="outline">{t.status || "open"}</Badge>
                </div>
              ))
            )}
          </CardContent>
        </Card>
      </div>

      <ClaimApprovalDialog
        claimId={id}
        claimTitle={claim.title}
        open={approvalOpen}
        onOpenChange={setApprovalOpen}
      />
      <ClaimAssessmentDialog
        claimId={id}
        claimTitle={claim.title}
        open={assessOpen}
        onOpenChange={setAssessOpen}
      />
      <ClaimTaskDialog
        claim={claim}
        open={taskOpen}
        onOpenChange={setTaskOpen}
        onCreated={loadTasks}
      />
    </div>
  );
};

export default ClaimDetailPage;
