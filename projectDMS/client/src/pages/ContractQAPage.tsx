import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import PageHeader from "@/components/ui/PageHeader";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import Badge from "@/components/ui/badge";
import { api } from "@/services/api";
import {
  askIterativeContractQuestion,
  ContractCitation,
  ContractIterativeQAResponse,
  IterationTrace,
} from "@/services/contracts-api";
import { extractErrorMessage } from "@/lib/error-logger";
import {
  Building2,
  Eye,
  FileText,
  Loader2,
  MessageSquare,
  Search,
} from "lucide-react";
import { usePinnedPageScope } from "@/hooks/useRegisterProjectScope";

type Organization = { id: string; name: string; shortName?: string | null };
type Project = { _id: string; name: string; organization_id: string };
type UploadOption = {
  document_id: string;
  upload_id?: string;
  filename: string;
  status?: string;
};

const ALL_FILES_ID = "__ALL_FILES__";
const QA_TIMEOUT_MS = 60_000;

const ANSWER_STYLE_PROMPT =
  "Answer the user's question using only the retrieved contract context. " +
  "Explain the contractual condition in your own language. " +
  "You may refer to relevant clause numbers naturally, but do not insert inline citations, bracketed references, " +
  "file names, page numbers, chunk IDs, or source metadata inside the answer. " +
  "Source metadata must be returned only in the separate sources list for display below the answer.";

function buildProbingQuestions(answer: string, question: string): string[] {
  const sourceText = (answer && answer.trim()) || (question && question.trim()) || "this requirement";
  const clauses = Array.from(
    new Set((sourceText.match(/(?:GCC|SCC|Clause)\s*[0-9][\w.\-]*/gi) || []).map((c) => c.trim()))
  ).slice(0, 2);

  const q1 = clauses.length
    ? `How do SCC modifications affect ${clauses[0]} and related obligations?`
    : `Are there SCC modifications to the GCC obligations related to this requirement?`;

  const q2 = `What approvals, tests, or notices are prerequisites before this can proceed?`;
  const q3 = `What timelines or milestones must be met, and who must be notified?`;
  const q4 = `What are the consequences or remedies if these conditions are not satisfied?`;
  const q5 = clauses.length > 1
    ? `Which clause prevails if ${clauses[0]} conflicts with ${clauses[1]}?`
    : `Which related clauses impact this requirement, and which one prevails if there is a conflict?`;

  return [q1, q2, q3, q4, q5];
}

const ContractQAPage: React.FC = () => {
  const navigate = useNavigate();
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [orgId, setOrgId] = useState<string>(() => window.localStorage.getItem("org_id") || "");
  const [projId, setProjId] = useState<string>(() => window.localStorage.getItem("proj_id") || "");
  // CL-4A: while the navbar selects a project, this page's picker follows it.
  usePinnedPageScope(orgId, setOrgId, projId, setProjId);
  const [uploads, setUploads] = useState<UploadOption[]>([]);
  const [selectedUpload, setSelectedUpload] = useState<string>("");
  const [question, setQuestion] = useState<string>("");
  const [answer, setAnswer] = useState<string>("");
  const [citations, setCitations] = useState<ContractCitation[]>([]);
  const [trace, setTrace] = useState<IterationTrace[]>([]);
  const [loading, setLoading] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showTrace, setShowTrace] = useState<boolean>(false);
  const askAbortRef = useRef<AbortController | null>(null);

  const loadOrganizations = useCallback(async () => {
    setFetching(true);
    try {
      const { data } = await api.get("/organizations", { params: { limit: 100 } });
      const list: Organization[] = (data?.organizations || data || []).map((o: any) => ({
        id: o.id ?? o._id ?? "",
        name: o.name ?? o.title ?? "",
        shortName: o.shortName ?? o.short_name ?? null,
      })).filter((o: Organization) => o.id && o.name);
      setOrganizations(list);
    } catch (e) {
      setOrganizations([]);
    } finally {
      setFetching(false);
    }
  }, []);

  const loadProjects = useCallback(async (organization_id: string) => {
    if (!organization_id) {
      setProjects([]);
      return;
    }
    setFetching(true);
    try {
      const { data } = await api.get("/projects", { params: { organization_id } });
      const list: Project[] = (data || []).map((p: any) => ({
        _id: p._id ?? p.id ?? "",
        name: p.name ?? "",
        organization_id: p.organization_id ?? organization_id,
      })).filter((p: Project) => p._id && p.name);
      setProjects(list);
    } catch (e) {
      setProjects([]);
    } finally {
      setFetching(false);
    }
  }, []);

  const loadUploads = useCallback(async (organization_id: string, project_id?: string) => {
    if (!organization_id) {
      setUploads([]);
      return;
    }
    setFetching(true);
    try {
      const { data } = await api.get("/contracts/list", {
        params: { organization_id, project_id: project_id || undefined, limit: 200, skip: 0 },
      });
      const list: UploadOption[] = (data?.uploads || [])
        .filter((u: any) => (u?.status || "").toLowerCase() === "completed")
        .map((u: any) => ({
        document_id: u.document_id ?? u._id ?? "",
        upload_id: u.upload_id ?? undefined,
        filename: u.filename ?? "contract",
        status: u.status ?? "unknown",
      })).filter((u: UploadOption) => u.document_id);
      setUploads(list);
    } catch (e) {
      setUploads([]);
    } finally {
      setFetching(false);
    }
  }, []);

  useEffect(() => {
    loadOrganizations().catch(() => {});
  }, [loadOrganizations]);

  useEffect(() => {
    if (orgId) {
      window.localStorage.setItem("org_id", orgId);
      loadProjects(orgId).catch(() => {});
    } else {
      window.localStorage.removeItem("org_id");
      setProjects([]);
      setUploads([]);
    }
  }, [orgId, loadProjects]);

  useEffect(() => {
    if (projId) {
      window.localStorage.setItem("proj_id", projId);
    } else {
      window.localStorage.removeItem("proj_id");
    }
    if (orgId) {
      loadUploads(orgId, projId).catch(() => {});
    }
  }, [projId, orgId, loadUploads]);

  useEffect(() => {
    if (selectedUpload === ALL_FILES_ID && uploads.length < 2) {
      setSelectedUpload("");
      return;
    }
    if (
      selectedUpload &&
      selectedUpload !== ALL_FILES_ID &&
      !uploads.some((upload) => upload.document_id === selectedUpload)
    ) {
      setSelectedUpload("");
    }
  }, [selectedUpload, uploads]);

  useEffect(() => {
    setError(null);
    setAnswer("");
    setCitations([]);
    setTrace([]);
    setShowTrace(false);
  }, [orgId, projId, selectedUpload]);

  const selectedUploadName = useMemo(() => {
    if (selectedUpload === ALL_FILES_ID) return "All completed files in project";
    return uploads.find((u) => u.document_id === selectedUpload)?.filename || "";
  }, [uploads, selectedUpload]);

  const handleAsk = useCallback(async () => {
    if (!orgId) {
      setError("Select an organization.");
      return;
    }
    if (!projId) {
      setError("Select a project.");
      return;
    }
    if (!selectedUpload) {
      setError("Select a contract document.");
      return;
    }
    if (!question.trim()) {
      setError("Enter a question.");
      return;
    }
    setError(null);
    setLoading(true);
    setAnswer("");
    setCitations([]);
    setTrace([]);
    setShowTrace(false);
    askAbortRef.current?.abort();
    const controller = new AbortController();
    let timedOut = false;
    const timeoutId = window.setTimeout(() => {
      timedOut = true;
      controller.abort();
    }, QA_TIMEOUT_MS);
    askAbortRef.current = controller;
    try {
      const document_id = selectedUpload === ALL_FILES_ID ? null : selectedUpload;
      const payload = {
        query: question,
        filters: {
          org_id: orgId,
          project_id: projId,
          document_id,
        },
        limit: 8,
        strategy: "rag_fusion" as const,
        backend: "auto" as const,
        use_enriched_text: true,
        answer_style: ANSWER_STYLE_PROMPT,
        max_tokens: 512,
        require_citations: false,
        max_iterations: 3,
        metadata_filters: {
          uploadType: "contract",
          document_type: "contract",
        },
      };
      const resp: ContractIterativeQAResponse = await askIterativeContractQuestion(payload, controller.signal);
      setAnswer(resp.answer);
      setCitations(resp.citations || []);
      setTrace(resp.trace || []);
    } catch (e: any) {
      if ((e?.name === "CanceledError" || e?.code === "ERR_CANCELED") && !timedOut) {
        return;
      }
      setError(
        timedOut
          ? "Question timed out. Try narrowing the scope or asking a shorter question."
          : extractErrorMessage(e, "Failed to generate answer.")
      );
    } finally {
      window.clearTimeout(timeoutId);
      if (askAbortRef.current === controller) {
        askAbortRef.current = null;
        setLoading(false);
      }
    }
  }, [orgId, projId, selectedUpload, question]);

  const handleCancel = useCallback(() => {
    askAbortRef.current?.abort();
    askAbortRef.current = null;
    setLoading(false);
  }, []);

  useEffect(() => {
    return () => {
      askAbortRef.current?.abort();
    };
  }, []);

  const probingQuestions = useMemo(() => buildProbingQuestions(answer, question), [answer, question]);

  return (
    <div className="max-w-6xl mx-auto p-6 space-y-6">
      <PageHeader
        icon={<MessageSquare className="h-6 w-6" />}
        title="Contract Q&A"
        description="Ask grounded questions against one completed contract, or all completed contracts in the selected project."
      />

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Building2 className="h-5 w-5" />
            Scope
          </CardTitle>
          <CardDescription>Select organization, project, and a completed contract scope.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">Organization *</label>
              <select
                data-testid="contract-qa-org-select"
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                value={orgId}
                onChange={(e) => {
                  setOrgId(e.target.value);
                  setProjId("");
                  setSelectedUpload("");
                }}
                disabled={fetching}
              >
                <option value="">Select organization</option>
                {organizations.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">Project *</label>
              <select
                data-testid="contract-qa-project-select"
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
                value={projId}
                onChange={(e) => {
                  setProjId(e.target.value);
                  setSelectedUpload("");
                }}
                disabled={!orgId || fetching}
              >
                <option value="">{orgId ? "Select project" : "Select organization first"}</option>
                {projects.map((p) => (
                  <option key={p._id} value={p._id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium text-gray-700">Contract File *</label>
            <select
              data-testid="contract-qa-file-select"
              className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
              value={selectedUpload}
              onChange={(e) => setSelectedUpload(e.target.value)}
              disabled={!orgId || fetching || uploads.length === 0}
            >
              <option value="">{uploads.length ? "Select completed contract" : "No completed contracts found"}</option>
              {uploads.length >= 2 && (
                <option value={ALL_FILES_ID}>All completed files in project</option>
              )}
              {uploads.map((u) => (
                <option key={u.document_id} value={u.document_id}>
                  {u.filename} {u.status ? `(${u.status})` : ""}
                </option>
              ))}
            </select>
            <p className="text-xs text-gray-500">
              Only completed contracts are available for QA.
            </p>
            {selectedUpload && selectedUpload !== ALL_FILES_ID && (
              <Button
                variant="outline"
                size="sm"
                className="gap-2"
                onClick={() => navigate(`/contracts/viewer/${selectedUpload}`)}
              >
                <Eye className="h-4 w-4" />
                View document
              </Button>
            )}
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-5 w-5" />
            Ask a question
          </CardTitle>
          <CardDescription>
            The answer is grounded only in the selected contract scope. Sources are shown for verification.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <textarea
            data-testid="contract-qa-question-input"
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            rows={3}
            className="w-full border rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
            placeholder="e.g., What are the conditions for issuance of taking over certificate?"
          />
          {error && <p className="text-sm text-red-600">{error}</p>}
        </CardContent>
        <CardFooter className="flex items-center justify-between">
          <div className="text-xs text-gray-500">
            Context: {selectedUploadName ? selectedUploadName : "No file selected"}
          </div>
          <div className="flex items-center gap-3">
            {loading && (
              <Button variant="outline" onClick={handleCancel}>
                Cancel
              </Button>
            )}
            <Button data-testid="contract-qa-submit" onClick={handleAsk} disabled={loading || fetching}>
              {loading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
              Ask
            </Button>
          </div>
        </CardFooter>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <FileText className="h-5 w-5" />
            Answer
          </CardTitle>
          <CardDescription>Grounded response. Source references are listed below.</CardDescription>
        </CardHeader>
        <CardContent data-testid="contract-qa-answer" className="space-y-3">
          {loading ? (
            <p className="text-sm text-gray-600 flex items-center gap-2">
              <Loader2 className="h-4 w-4 animate-spin" /> Generating answer...
            </p>
          ) : answer ? (
            <div className="prose prose-sm max-w-none whitespace-pre-wrap">{answer}</div>
          ) : (
            <p className="text-sm text-gray-500">No answer yet.</p>
          )}

          {citations.length > 0 && (
            <div className="space-y-2">
              <h4 className="text-sm font-semibold">Sources</h4>
              <ul className="space-y-1">
                {citations.map((c, idx) => (
                  <li key={c.chunk_id || idx} className="text-sm text-gray-700 flex items-start gap-2">
                    <Badge variant="outline">#{idx + 1}</Badge>
                      <div className="flex-1">
                      <div className="flex items-center justify-between gap-2">
                      <div className="font-medium">
                        {c.clause_number ? `Clause ${c.clause_number}` : c.document_title || selectedUploadName || "Contract"}
                        {c.clause_title ? ` - ${c.clause_title}` : ""}
                      </div>
                      {c.document_id && (
                        <button
                          type="button"
                          onClick={() => navigate(`/contracts/viewer/${c.document_id}`)}
                          className="shrink-0 inline-flex items-center gap-1 text-xs text-blue-600 hover:text-blue-800 hover:underline"
                        >
                          <Eye className="h-3.5 w-3.5" />
                          View
                        </button>
                      )}
                      </div>
                      <div className="text-xs text-gray-500">
                        {c.letter_no ? `${c.letter_no} - ` : ""}
                        {c.file_name ? `${c.file_name} - ` : ""}
                        {c.page_numbers?.length
                          ? `p.${c.page_numbers.join(", ")}`
                          : c.page
                          ? `p.${c.page}`
                          : "page n/a"}{" "}
                        - chunk {c.chunk_id || "n/a"} - score {c.score?.toFixed(3) ?? "n/a"}
                      </div>
                      {c.section_heading && (
                        <div className="text-xs text-gray-500">
                          Section: {c.section_heading}
                        </div>
                      )}
                      <div className="text-xs text-gray-600">{c.snippet}</div>
                    </div>
                  </li>
                ))}
              </ul>
            </div>
          )}

          {trace.length > 0 && (
            <div className="space-y-2">
              <button
                type="button"
                onClick={() => setShowTrace((prev) => !prev)}
                className="text-sm font-semibold text-gray-700 hover:text-gray-900"
              >
                {showTrace ? "Hide debug trace" : "Show debug trace"}
              </button>
              {showTrace && (
                <div className="space-y-3">
                  {trace.map((step) => (
                    <div key={step.iteration} className="border rounded-md p-3 bg-gray-50">
                      <div className="text-xs font-semibold text-gray-700">Iteration {step.iteration}</div>
                      {step.queries?.length ? (
                        <div className="text-xs text-gray-700">
                          <span className="font-medium">Queries:</span> {step.queries.join(" | ")}
                        </div>
                      ) : null}
                      {step.retrieved_ids?.length ? (
                        <div className="text-xs text-gray-700">
                          <span className="font-medium">Citations:</span> {step.retrieved_ids.join(", ")}
                        </div>
                      ) : null}
                      {step.critique ? (
                        <div className="text-xs text-gray-700">
                          <span className="font-medium">Critique:</span> {step.critique}
                        </div>
                      ) : null}
                      {step.refinements && step.refinements.length ? (
                        <div className="text-xs text-gray-700">
                          <span className="font-medium">Refinements:</span> {step.refinements.join(" | ")}
                        </div>
                      ) : null}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="space-y-2">
            <h4 className="text-sm font-semibold">Probing questions</h4>
            <ul className="list-disc pl-5 text-sm text-gray-700">
              {probingQuestions.map((pq, idx) => (
                <li key={idx}>{pq}</li>
              ))}
            </ul>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default ContractQAPage;
