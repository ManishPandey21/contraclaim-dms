import React, { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Textarea } from "@/components/ui/textarea";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  AlertTriangle,
  CheckCircle2,
  Download,
  FileText,
  Loader2,
  Pencil,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
  XCircle,
} from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { Input } from "@/components/ui/input";
import { toast } from "sonner";
import {
  addAppraisalComment,
  approveAppraisal,
  AppraisalJob,
  AppraisalReport,
  deleteAppraisal,
  editAppraisal,
  exportAppraisalDocx,
  exportAppraisalPdf,
  generateAppraisal,
  getAppraisal,
  getAppraisalJob,
  getExistingAppraisal,
  listAppraisals,
  rejectAppraisal,
} from "@/services/contracts-api";
import AppraisalRegisters from "@/components/contract-appraisal/AppraisalRegisters";
import ClauseLibrary from "@/components/contract-appraisal/ClauseLibrary";
import { enhancedApi } from "@/services/enhanced-api";
import { listContractUploads, type ContractUpload } from "@/services/contracts-api";

const ALL_CONTRACTS = "__all__";

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
  const [contractDocId, setContractDocId] = useState(ALL_CONTRACTS);
  const [orgs, setOrgs] = useState<{ id: string; name: string }[]>([]);
  const [projects, setProjects] = useState<{ id: string; name: string; organization_id: string }[]>([]);
  const [contracts, setContracts] = useState<ContractUpload[]>([]);
  const [job, setJob] = useState<AppraisalJob | null>(null);
  const [reports, setReports] = useState<AppraisalReport[]>([]);
  const [selected, setSelected] = useState<AppraisalReport | null>(null);
  const [existingForSelection, setExistingForSelection] = useState<AppraisalReport | null>(null);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [editSummary, setEditSummary] = useState("");
  const [editMarkdown, setEditMarkdown] = useState("");
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

  // Organisations + projects the user can access (cascading dropdowns).
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const [os, ps] = await Promise.all([
          enhancedApi.getOrganizations().catch(() => []),
          enhancedApi.getProjects().catch(() => []),
        ]);
        if (!active) return;
        setOrgs((os || []).map((o: any) => ({ id: String(o._id || o.id || ""), name: o.name || "Organization" })));
        setProjects(
          (ps || []).map((p: any) => ({
            id: String(p._id || p.id || ""),
            name: p.name || "Project",
            organization_id: String(p.organization_id || ""),
          })),
        );
      } catch {
        /* selectors are best-effort */
      }
    })();
    return () => {
      active = false;
    };
  }, []);

  // Uploaded contract documents for the selected org/project (the appraisal
  // source). Only completed contracts are appraisable.
  useEffect(() => {
    let active = true;
    setContractDocId(ALL_CONTRACTS);
    if (!projectId) {
      setContracts([]);
      return;
    }
    (async () => {
      try {
        const list = await listContractUploads({
          organization_id: orgId || undefined,
          project_id: projectId,
          limit: 200,
        });
        if (active) setContracts(list.filter((c) => String(c.status).toLowerCase() === "completed"));
      } catch {
        if (active) setContracts([]);
      }
    })();
    return () => {
      active = false;
    };
  }, [orgId, projectId]);

  // Generate-once: whenever the selection changes, look for an already-saved
  // report and show it. Generation is blocked while one exists.
  useEffect(() => {
    let active = true;
    if (!projectId) {
      setExistingForSelection(null);
      return;
    }
    (async () => {
      try {
        const report = await getExistingAppraisal({
          organization_id: orgId || undefined,
          project_id: projectId,
          document_id: contractDocId !== ALL_CONTRACTS ? contractDocId : undefined,
        });
        if (!active) return;
        setExistingForSelection(report);
        if (report) setSelected(report);
      } catch {
        if (active) setExistingForSelection(null);
      }
    })();
    return () => {
      active = false;
    };
  }, [orgId, projectId, contractDocId]);

  const projectsForOrg = orgId
    ? projects.filter((p) => p.organization_id === orgId)
    : projects;

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
              setExistingForSelection(report);
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
    if (!projectId) {
      toast.error("Select a project");
      return;
    }
    if (existingForSelection) {
      toast.info("An appraisal already exists for this selection. Delete it to regenerate.");
      setSelected(existingForSelection);
      return;
    }
    setBusy(true);
    try {
      const ids = contractDocId && contractDocId !== ALL_CONTRACTS ? [contractDocId] : [];
      const j = await generateAppraisal({
        organization_id: orgId || undefined,
        project_id: projectId,
        document_ids: ids,
      });
      setJob(j);
      setSelected(null);
      pollJob(j._id);
    } catch (e: any) {
      // Server enforces generate-once: a 409 means a report already exists.
      const existingId = e?.response?.data?.detail?.report_id;
      if (e?.response?.status === 409 && existingId) {
        try {
          const report = await getAppraisal(existingId);
          setSelected(report);
          setExistingForSelection(report);
          toast.info("An appraisal already exists for this selection.");
        } catch {
          toast.error("An appraisal already exists for this selection.");
        }
      } else {
        toast.error(e?.response?.data?.detail || "Failed to start appraisal");
      }
    } finally {
      setBusy(false);
    }
  };

  const onSaveEdit = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      const updated = await editAppraisal(selected._id, {
        executive_summary: editSummary,
        full_report_markdown: editMarkdown,
      });
      setSelected(updated);
      setExistingForSelection(updated);
      setEditing(false);
      toast.success("Appraisal saved");
      await loadReports();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to save");
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async () => {
    if (!selected) return;
    setBusy(true);
    try {
      await deleteAppraisal(selected._id);
      toast.success("Appraisal deleted — you can generate again");
      setSelected(null);
      setExistingForSelection(null);
      await loadReports();
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "Failed to delete");
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

  const downloadBlob = (blob: Blob, ext: string) => {
    if (!selected) return;
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `contract-appraisal-v${selected.report_version}.${ext}`;
    a.click();
    URL.revokeObjectURL(url);
  };

  const onExportDocx = async () => {
    if (!selected) return;
    try {
      downloadBlob(await exportAppraisalDocx(selected._id), "docx");
    } catch {
      toast.error("DOCX export failed");
    }
  };

  const onExportPdf = async () => {
    if (!selected) return;
    try {
      downloadBlob(await exportAppraisalPdf(selected._id), "pdf");
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || "PDF export failed");
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
              <Label>Organisation</Label>
              <Select
                value={orgId}
                onValueChange={(v) => {
                  setOrgId(v);
                  setProjectId("");
                }}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select organisation" />
                </SelectTrigger>
                <SelectContent>
                  {orgs.map((o) => (
                    <SelectItem key={o.id} value={o.id}>
                      {o.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Project</Label>
              <Select value={projectId} onValueChange={setProjectId} disabled={projectsForOrg.length === 0}>
                <SelectTrigger>
                  <SelectValue placeholder={orgId ? "Select project" : "Select organisation first"} />
                </SelectTrigger>
                <SelectContent>
                  {projectsForOrg.map((p) => (
                    <SelectItem key={p.id} value={p.id}>
                      {p.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div>
              <Label>Contract document</Label>
              <Select value={contractDocId} onValueChange={setContractDocId} disabled={!projectId}>
                <SelectTrigger>
                  <SelectValue placeholder="All project contracts" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value={ALL_CONTRACTS}>All project contracts</SelectItem>
                  {contracts.map((c) => (
                    <SelectItem key={c.document_id} value={c.document_id}>
                      {c.filename || c.document_id}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              {projectId && contracts.length === 0 && (
                <p className="mt-1 text-xs text-muted-foreground">
                  No completed contracts. Upload one under “Upload Contract”.
                </p>
              )}
            </div>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button onClick={onGenerate} disabled={busy || !!jobActive || !projectId || !!existingForSelection}>
              {busy || jobActive ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : (
                <Sparkles className="mr-2 h-4 w-4" />
              )}
              Generate Appraisal Report
            </Button>
            <Button variant="outline" onClick={() => void loadReports()} disabled={!projectId}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Load existing
            </Button>
            {existingForSelection && (
              <span className="text-sm text-amber-600">
                An appraisal already exists for this selection — shown below. Delete it to regenerate.
              </span>
            )}
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
              {!selected.is_locked && !editing && (
                <Button
                  variant="outline"
                  onClick={() => {
                    setEditSummary(selected.executive_summary || "");
                    setEditMarkdown(selected.full_report_markdown || "");
                    setEditing(true);
                  }}
                  disabled={busy}
                >
                  <Pencil className="mr-2 h-4 w-4" />
                  Edit
                </Button>
              )}
              <Button variant="outline" onClick={onExportDocx} disabled={busy}>
                <Download className="mr-2 h-4 w-4" />
                Export DOCX
              </Button>
              <Button variant="outline" onClick={onExportPdf} disabled={busy}>
                <Download className="mr-2 h-4 w-4" />
                Export PDF
              </Button>
              <AlertDialog>
                <AlertDialogTrigger asChild>
                  <Button variant="outline" className="text-destructive" disabled={busy}>
                    <Trash2 className="mr-2 h-4 w-4" />
                    Delete
                  </Button>
                </AlertDialogTrigger>
                <AlertDialogContent>
                  <AlertDialogHeader>
                    <AlertDialogTitle>Delete this appraisal?</AlertDialogTitle>
                    <AlertDialogDescription>
                      This permanently removes the report and its registers. You can then
                      generate a fresh appraisal for this selection.
                    </AlertDialogDescription>
                  </AlertDialogHeader>
                  <AlertDialogFooter>
                    <AlertDialogCancel>Cancel</AlertDialogCancel>
                    <AlertDialogAction onClick={onDelete}>Delete</AlertDialogAction>
                  </AlertDialogFooter>
                </AlertDialogContent>
              </AlertDialog>
            </div>

            {editing ? (
              <div className="space-y-3 rounded-md border p-3">
                <div>
                  <Label>Executive summary</Label>
                  <Textarea value={editSummary} onChange={(e) => setEditSummary(e.target.value)} rows={4} />
                </div>
                <div>
                  <Label>Full report (markdown)</Label>
                  <Textarea
                    value={editMarkdown}
                    onChange={(e) => setEditMarkdown(e.target.value)}
                    rows={18}
                    className="font-mono text-xs"
                  />
                </div>
                <div className="flex gap-2">
                  <Button onClick={onSaveEdit} disabled={busy}>
                    {busy ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-2 h-4 w-4" />}
                    Save
                  </Button>
                  <Button variant="outline" onClick={() => setEditing(false)} disabled={busy}>
                    Cancel
                  </Button>
                </div>
              </div>
            ) : (
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
                      {s.citations.map((c, i) => {
                        const label = [
                          c.document_title || c.file_name,
                          c.clause_number ? `Clause ${c.clause_number}` : null,
                          c.page != null ? `p.${c.page}` : null,
                        ]
                          .filter(Boolean)
                          .join(" · ");
                        return (
                          <li key={i}>
                            {c.document_id ? (
                              <Link
                                to={`/documentviewer/${c.document_id}`}
                                className="text-blue-600 hover:underline"
                                title="Open source document"
                              >
                                {label}
                              </Link>
                            ) : (
                              label
                            )}
                          </li>
                        );
                      })}
                    </ul>
                  )}
                </div>
              ))}
            </div>
            )}

            <div className="border-t pt-3">
              <AppraisalRegisters
                reportId={selected._id}
                organizationId={selected.organization_id || undefined}
                projectId={selected.project_id || undefined}
              />
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

      <ClauseLibrary organizationId={orgId.trim() || undefined} projectId={projectId.trim() || undefined} />
    </div>
  );
};

export default ContractAppraisalPage;
