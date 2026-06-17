import React, { useCallback, useEffect, useRef, useState } from "react";
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
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileText,
  Loader2,
  RefreshCw,
  Sparkles,
  XCircle,
} from "lucide-react";
import { toast } from "sonner";
import {
  addAppraisalComment,
  approveAppraisal,
  AppraisalJob,
  AppraisalReport,
  exportAppraisalDocx,
  generateAppraisal,
  getAppraisal,
  getAppraisalJob,
  listAppraisals,
  regenerateAppraisal,
  rejectAppraisal,
} from "@/services/contracts-api";

const TERMINAL_JOB = new Set(["completed", "failed", "cancelled"]);

const RISK_COLOR: Record<string, string> = {
  low: "bg-green-600",
  medium: "bg-amber-500",
  high: "bg-orange-600",
  critical: "bg-red-700",
};

const STATUS_COLOR: Record<string, string> = {
  draft: "bg-gray-500",
  under_review: "bg-amber-500",
  approved: "bg-green-600",
  superseded: "bg-gray-700",
  rejected: "bg-red-500",
};

function completenessBadge(status: string) {
  if (status === "complete")
    return <Badge className="bg-green-600">Complete document set</Badge>;
  if (status === "incomplete")
    return <Badge className="bg-amber-500">Incomplete document set</Badge>;
  return <Badge variant="outline">Completeness needs review</Badge>;
}

const ContractAppraisalPage: React.FC = () => {
  const [orgId, setOrgId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [documentIds, setDocumentIds] = useState("");
  const [job, setJob] = useState<AppraisalJob | null>(null);
  const [reports, setReports] = useState<AppraisalReport[]>([]);
  const [selected, setSelected] = useState<AppraisalReport | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadReports = useCallback(async () => {
    if (!projectId.trim()) return;
    try {
      setReports(
        await listAppraisals({
          organization_id: orgId.trim() || undefined,
          project_id: projectId.trim(),
        }),
      );
    } catch {
      /* list is best-effort */
    }
  }, [orgId, projectId]);

  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
  }, []);

  const pollJob = useCallback(
    (jobId: string) => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = setInterval(async () => {
        try {
          const j = await getAppraisalJob(jobId);
          setJob(j);
          if (TERMINAL_JOB.has(j.status)) {
            if (pollRef.current) clearInterval(pollRef.current);
            if (j.status === "completed" && j.report_id) {
              toast.success("Appraisal report generated");
              const report = await getAppraisal(j.report_id);
              setSelected(report);
              await loadReports();
            } else if (j.status === "failed") {
              toast.error(j.error_message || "Appraisal generation failed");
            }
          }
        } catch {
          if (pollRef.current) clearInterval(pollRef.current);
        }
      }, 2000);
    },
    [loadReports],
  );

  const onGenerate = async () => {
    if (!projectId.trim()) {
      toast.error("Project ID is required");
      return;
    }
    setBusy(true);
    try {
      const ids = documentIds
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean);
      const j = await generateAppraisal({
        organization_id: orgId.trim() || undefined,
        project_id: projectId.trim(),
        document_ids: ids,
      });
      setJob(j);
      setSelected(null);
      pollJob(j._id);
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to start appraisal");
    } finally {
      setBusy(false);
    }
  };

  const act = async (fn: () => Promise<AppraisalReport>, ok: string) => {
    setBusy(true);
    try {
      setSelected(await fn());
      toast.success(ok);
      await loadReports();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Action failed");
    } finally {
      setBusy(false);
    }
  };

  const onRegenerate = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const j = await regenerateAppraisal(selected._id);
      setJob(j);
      pollJob(j._id);
      toast.info("Regenerating — a new version will be created");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Regenerate failed");
    } finally {
      setBusy(false);
    }
  };

  const onExport = async () => {
    if (!selected) return;
    try {
      const blob = await exportAppraisalDocx(selected._id);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `contract-appraisal-v${selected.report_version}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch {
      toast.error("Export failed");
    }
  };

  const onComment = async () => {
    if (!selected || !comment.trim()) return;
    try {
      await addAppraisalComment(selected._id, comment.trim());
      setComment("");
      toast.success("Comment added");
      setSelected(await getAppraisal(selected._id));
    } catch {
      toast.error("Failed to add comment");
    }
  };

  const jobActive = job && !TERMINAL_JOB.has(job.status);

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center gap-2">
        <Sparkles className="h-6 w-6 text-indigo-500" />
        <div>
          <h1 className="text-2xl font-bold">AI Contract Appraisal</h1>
          <p className="text-sm text-muted-foreground">
            Generate a clause-grounded appraisal of the project&apos;s contract documents.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Generate appraisal</CardTitle>
          <CardDescription>
            Scoped to your organisation and project. Leave documents blank to appraise the whole contract set.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div>
              <Label>Organisation ID (optional)</Label>
              <Input value={orgId} onChange={(e) => setOrgId(e.target.value)} placeholder="defaults to yours" />
            </div>
            <div>
              <Label>Project ID</Label>
              <Input value={projectId} onChange={(e) => setProjectId(e.target.value)} placeholder="required" />
            </div>
            <div>
              <Label>Document IDs (optional, comma-separated)</Label>
              <Input value={documentIds} onChange={(e) => setDocumentIds(e.target.value)} placeholder="d1, d2" />
            </div>
          </div>
          <div className="flex gap-2">
            <Button onClick={onGenerate} disabled={busy || !!jobActive || !projectId.trim()}>
              {busy || jobActive ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-2 h-4 w-4" />
              )}
              Generate Appraisal Report
            </Button>
            <Button variant="outline" onClick={() => void loadReports()} disabled={!projectId.trim()}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Load existing
            </Button>
          </div>

          {jobActive && (
            <div className="space-y-1 pt-2">
              <div className="flex justify-between text-sm text-muted-foreground">
                <span>{job?.current_step || "Working…"}</span>
                <span>{job?.progress ?? 0}%</span>
              </div>
              <Progress value={job?.progress ?? 0} />
            </div>
          )}
        </CardContent>
      </Card>

      {reports.length > 0 && !selected && (
        <Card>
          <CardHeader>
            <CardTitle>Reports</CardTitle>
            <CardDescription>Select a version to review.</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2">
            {reports.map((r) => (
              <button
                key={r._id}
                type="button"
                onClick={() => void getAppraisal(r._id).then(setSelected)}
                className="flex w-full items-center justify-between rounded-md border p-3 text-left hover:bg-muted/40"
              >
                <span className="flex items-center gap-2">
                  <FileText className="h-4 w-4" />
                  Version {r.report_version}
                  <Badge className={STATUS_COLOR[r.status]}>{r.status}</Badge>
                </span>
                <span className="text-sm text-muted-foreground">
                  {Math.round(r.confidence_score * 100)}% supported
                </span>
              </button>
            ))}
          </CardContent>
        </Card>
      )}

      {selected && (
        <Card>
          <CardHeader>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2">
                <CardTitle>Appraisal — Version {selected.report_version}</CardTitle>
                <Badge className={STATUS_COLOR[selected.status]}>{selected.status}</Badge>
                {selected.is_locked && <Badge variant="outline">Locked</Badge>}
                {completenessBadge(selected.document_completeness_status)}
                {selected.overall_risk_rating && (
                  <Badge className={RISK_COLOR[selected.overall_risk_rating] || "bg-gray-500"}>
                    Risk: {selected.overall_risk_rating}
                  </Badge>
                )}
                <Badge variant="outline">
                  {Math.round(selected.confidence_score * 100)}% supported
                </Badge>
              </div>
              <Button variant="ghost" size="sm" onClick={() => setSelected(null)}>
                Back to list
              </Button>
            </div>
            {selected.missing_documents.length > 0 && (
              <div className="mt-2 flex items-start gap-2 rounded-md border border-amber-300 bg-amber-50 p-2 text-sm text-amber-800">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <span>
                  Generated with an incomplete document set. Missing:{" "}
                  {selected.missing_documents.join(", ")}. Some findings may require human verification.
                </span>
              </div>
            )}
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {!selected.is_locked && selected.status !== "approved" && (
                <>
                  <Button onClick={() => act(() => approveAppraisal(selected._id), "Approved & locked")} disabled={busy}>
                    <CheckCircle2 className="mr-2 h-4 w-4" />
                    Approve
                  </Button>
                  <Button variant="outline" onClick={() => act(() => rejectAppraisal(selected._id), "Rejected")} disabled={busy}>
                    <XCircle className="mr-2 h-4 w-4" />
                    Reject
                  </Button>
                </>
              )}
              <Button variant="outline" onClick={onRegenerate} disabled={busy || !!jobActive}>
                <RefreshCw className="mr-2 h-4 w-4" />
                Regenerate (new version)
              </Button>
              <Button variant="outline" onClick={onExport} disabled={busy}>
                <Download className="mr-2 h-4 w-4" />
                Export DOCX
              </Button>
            </div>

            <div className="space-y-4">
              {selected.sections.map((s) => (
                <div key={s.key} className="rounded-md border p-3">
                  <div className="mb-1 flex items-center gap-2">
                    <h3 className="font-semibold">{s.title}</h3>
                    {s.supported ? (
                      <Badge variant="outline" className="border-green-400 text-green-700">
                        {s.citations.length} citation{s.citations.length === 1 ? "" : "s"}
                      </Badge>
                    ) : (
                      <Badge variant="outline" className="border-amber-400 text-amber-700">
                        Requires human review
                      </Badge>
                    )}
                  </div>
                  <p className="whitespace-pre-wrap text-sm">{s.markdown}</p>
                  {s.citations.length > 0 && (
                    <ul className="mt-2 space-y-1 text-xs text-muted-foreground">
                      {s.citations.map((c, i) => (
                        <li key={i}>
                          {[c.document_title || c.file_name, c.clause_number ? `Clause ${c.clause_number}` : null, c.page != null ? `p.${c.page}` : null]
                            .filter(Boolean)
                            .join(" · ")}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
              ))}
            </div>

            <div className="space-y-2 border-t pt-3">
              <Label>Add review comment</Label>
              <Textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} />
              <Button size="sm" onClick={onComment} disabled={!comment.trim()}>
                Add comment
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default ContractAppraisalPage;
