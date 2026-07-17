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
  ArrowLeft,
  ClipboardList,
  Download,
  FileText,
  Loader2,
  ShieldCheck,
  Sparkles,
} from "lucide-react";
import { toast } from "sonner";
import {
  ClaimDTO,
  ClaimStatus,
  downloadEvidenceBundle,
  getClaim,
} from "@/services/claims-api";
import { getTasks, TaskDTO } from "@/services/tasks-api";
import ClaimApprovalDialog from "@/components/claims/ClaimApprovalDialog";
import ClaimAssessmentDialog from "@/components/claims/ClaimAssessmentDialog";
import ClaimTaskDialog from "@/components/claims/ClaimTaskDialog";

const STATUS_COLOR: Record<ClaimStatus, string> = {
  draft: "bg-gray-500",
  notified: "bg-blue-500",
  submitted: "bg-indigo-500",
  under_review: "bg-amber-500",
  agreed: "bg-green-600",
  rejected: "bg-red-500",
  disputed: "bg-red-700",
  closed: "bg-gray-700",
};

const TYPE_LABELS: Record<string, string> = {
  eot: "Extension of Time",
  variation: "Variation",
  payment_ipc: "Payment / IPC",
  loss_expense: "Loss & Expense",
  acceleration: "Acceleration",
  defect: "Defect",
  other: "Other",
};

const fmtDate = (d?: string | null) => (d ? new Date(d).toLocaleDateString() : "—");
const fmtAmount = (n?: number | null) => (n != null ? n.toLocaleString() : "—");

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
    } catch {
      toast.error("Failed to load claim");
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
    } catch {
      toast.error("Failed to export evidence bundle");
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
            <Badge className={STATUS_COLOR[claim.status]}>{claim.status}</Badge>
            <Badge variant="outline">{TYPE_LABELS[claim.type] || claim.type}</Badge>
          </div>
          {claim.claim_ref && (
            <CardDescription className="font-mono text-xs">{claim.claim_ref}</CardDescription>
          )}
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-4 sm:grid-cols-3">
          <Field label="Amount claimed" value={fmtAmount(claim.amount_claimed)} />
          <Field label="Amount agreed" value={fmtAmount(claim.amount_agreed)} />
          <Field label="Currency" value={claim.currency || "—"} />
          <Field label="Event date" value={fmtDate(claim.event_date)} />
          <Field label="Notice date" value={fmtDate(claim.notice_date)} />
          <Field label="Response due" value={fmtDate(claim.response_due_date)} />
          <Field label="EOT days claimed" value={claim.eot_days_claimed ?? "—"} />
          <Field label="EOT days granted" value={claim.eot_days_granted ?? "—"} />
          <Field label="Responsible party" value={claim.responsible_party_id || "—"} />
        </CardContent>
      </Card>

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
            {claim.linked_document_ids.length === 0 && claim.linked_letter_ids.length === 0 ? (
              <p className="text-sm text-muted-foreground">No linked correspondence yet.</p>
            ) : (
              <>
                {claim.linked_document_ids.map((d) => (
                  <Link
                    key={d}
                    to={`/documentviewer/${d}`}
                    className="flex items-center gap-2 rounded-md border p-2 text-sm hover:bg-muted/40"
                  >
                    <FileText className="h-4 w-4" /> Document {d}
                  </Link>
                ))}
                {claim.linked_letter_ids.map((l) => (
                  <Link
                    key={l}
                    to={`/letters/${l}/input`}
                    className="flex items-center gap-2 rounded-md border p-2 text-sm hover:bg-muted/40"
                  >
                    <FileText className="h-4 w-4" /> Letter {l}
                  </Link>
                ))}
              </>
            )}
            {claim.contract_clauses.length > 0 && (
              <p className="pt-2 text-xs text-muted-foreground">
                Clauses: {claim.contract_clauses.join(", ")}
              </p>
            )}
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
