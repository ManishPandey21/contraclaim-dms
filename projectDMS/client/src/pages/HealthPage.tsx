import React, { useEffect, useMemo, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Loader2, ShieldAlert, Database } from "lucide-react";
import { joinApiUrl } from "@/config/api";
import { toast } from "sonner";
import { enhancedApi as api, Organization, Project } from "@/services/enhanced-api";

type VectorStoreStatus = {
  mongo_chunk_count?: number;
  qdrant_chunk_count?: number | null;
  qdrant_available?: boolean;
  qdrant_enabled?: boolean;
  vector_store_enabled?: boolean;
  qdrant_latency_ms?: number | null;
  qdrant_retries?: number;
  qdrant_exact?: boolean;
  qdrant_error?: string | null;
  qdrant_timeout_s?: number;
  qdrant_method?: string | null;
};

type SyncStatus = {
  tracked_documents: number;
  pending: number;
  mismatch: number;
  errors: number;
  stale: number;
};

type ReferencesStatus = {
  documents_with_references: number;
  documents_missing_backlinks: number;
};

type HealthStatus = {
  status: string;
  issues: string[];
  timestamp: string;
  vector_store: VectorStoreStatus;
  documents?: {
    mongo?: number | null;
    qdrant?: number | null;
    falkor?: number | null;
  };
  sync_status: SyncStatus;
  references: ReferencesStatus;
  falkor?: {
    enabled: boolean;
    available: boolean;
    reason?: string;
    nodes?: number;
    edges?: number;
    avg_degree?: number;
    per_org?: Array<{
      org: string | null;
      project: string | null;
      nodes: number;
      edges: number;
      avg_degree: number;
      org_name?: string | null;
      project_name?: string | null;
    }>;
  };
};

type LanggraphLLMConfig = {
  drafter_model: string;
  reviewer_model: string;
  plan_model: string;
  draft_prompt_template: string;
  plan_prompt_template?: string;
  available_models: string[];
};

const HealthPage: React.FC = () => {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [orgId, setOrgId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [repairLimit, setRepairLimit] = useState<number>(10);
  const [orgOptions, setOrgOptions] = useState<Organization[]>([]);
  const [projectOptions, setProjectOptions] = useState<Project[]>([]);
  const [qdrantMethod, setQdrantMethod] = useState<"approx" | "exact">("approx");
  const [llmConfig, setLlmConfig] = useState<LanggraphLLMConfig | null>(null);
  const [llmSaving, setLlmSaving] = useState(false);
  const roles = (localStorage.getItem("user_roles") || "").toLowerCase();
  const isSuperadmin = roles.includes("superadmin");

  const fetchHealth = async () => {
    setLoading(true);
    try {
      const token = localStorage.getItem("accessToken") || "";
      const headers: Record<string, string> = token
        ? { Authorization: `Bearer ${token}` }
        : {};
      const params = new URLSearchParams({ method: qdrantMethod });
      const res = await fetch(joinApiUrl(`/storage-sync/status?${params.toString()}`), { headers });
      if (!res.ok) throw new Error(`Failed to fetch health (${res.status})`);
      const data = (await res.json()) as HealthStatus;
      setHealth(data);
    } catch (err) {
      console.error(err);
      toast.error((err as Error)?.message || "Failed to load health status");
    } finally {
      setLoading(false);
    }
  };

  const fetchLlmConfig = async () => {
    try {
      const token = localStorage.getItem("accessToken") || "";
      const headers: Record<string, string> = token
        ? { Authorization: `Bearer ${token}` }
        : {};
      const res = await fetch(joinApiUrl("/ai-assistant/langgraph/config"), { headers });
      if (!res.ok) throw new Error(`Failed to fetch LangGraph config (${res.status})`);
      const data = (await res.json()) as LanggraphLLMConfig;
      setLlmConfig(data);
    } catch (err) {
      console.error(err);
      toast.error((err as Error)?.message || "Failed to load LangGraph config");
    }
  };

  const saveLlmConfig = async () => {
    if (!llmConfig) return;
    setLlmSaving(true);
    try {
      const token = localStorage.getItem("accessToken") || "";
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (token) headers["Authorization"] = `Bearer ${token}`;
      const res = await fetch(joinApiUrl("/ai-assistant/langgraph/config"), {
        method: "PUT",
        headers,
        body: JSON.stringify({
          drafter_model: llmConfig.drafter_model,
          reviewer_model: llmConfig.reviewer_model,
          draft_prompt_template: llmConfig.draft_prompt_template,
          plan_model: llmConfig.plan_model,
          plan_prompt_template: llmConfig.plan_prompt_template,
        }),
      });
      if (!res.ok) {
        const detail = await res.text();
        throw new Error(detail || `Failed to save LangGraph config (${res.status})`);
      }
      const data = (await res.json()) as LanggraphLLMConfig;
      setLlmConfig(data);
      toast.success("LangGraph drafting configuration saved");
    } catch (err) {
      console.error(err);
      toast.error((err as Error)?.message || "Failed to save LangGraph config");
    } finally {
      setLlmSaving(false);
    }
  };

  const runBulkRepair = async () => {
    if (!orgId && !projectId) {
      toast.error("Provide org or project to scope bulk repair.");
      return;
    }
    setRepairing(true);
    try {
      const token = localStorage.getItem("accessToken") || "";
      const headers: Record<string, string> = {
        "Content-Type": "application/json",
      };
      if (token) headers["Authorization"] = `Bearer ${token}`;

      const res = await fetch(joinApiUrl("/storage-sync/resync-bulk"), {
        method: "POST",
        headers,
        body: JSON.stringify({
          org_id: orgId || null,
          project_id: projectId || null,
          limit: repairLimit || 10,
        }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data?.detail || `Bulk repair failed (${res.status})`);
      }
      const successCount = data?.succeeded ?? 0;
      const failCount = data?.failed ?? 0;
      if (failCount > 0) {
        const firstError = data?.errors?.[0]?.error;
        toast.error(`Bulk repair partial: ${successCount} ok, ${failCount} failed.${firstError ? ` First error: ${firstError}` : ""}`);
      } else {
        toast.success(`Bulk repair complete: ${successCount} ok, ${failCount} failed.`);
      }
      fetchHealth().catch(() => {});
    } catch (err) {
      console.error(err);
      toast.error((err as Error)?.message || "Bulk repair failed");
    } finally {
      setRepairing(false);
    }
  };

  useEffect(() => {
    if (isSuperadmin) {
      fetchHealth().catch(() => {});
      fetchLlmConfig().catch(() => {});
    }
  }, [isSuperadmin]);

  useEffect(() => {
    if (!isSuperadmin) return;
    const loadOptions = async () => {
      try {
        const [orgs, projects] = await Promise.all([
          api.getOrganizations().catch(() => []),
          api.getProjects().catch(() => []),
        ]);
        setOrgOptions(orgs || []);
        setProjectOptions(projects || []);
      } catch {
        // ignore silently; UI will fall back to text entry if needed
      }
    };
    loadOptions().catch(() => {});
  }, [isSuperadmin]);

  const filteredProjects = useMemo(() => {
    if (!orgId) return projectOptions;
    return projectOptions.filter((p) => (p as any).organization_id === orgId);
  }, [orgId, projectOptions]);

  if (!isSuperadmin) {
    return (
      <div className="p-6">
        <Card>
          <CardHeader>
            <CardTitle>Health Dashboard</CardTitle>
            <CardDescription>Superadmin access required.</CardDescription>
          </CardHeader>
          <CardContent className="flex items-center gap-2 text-red-600">
            <ShieldAlert className="h-5 w-5" />
            You do not have permission to view this page.
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-semibold">System Health</h1>
          <p className="text-sm text-muted-foreground">
            Vector sync, reference backlinks, and database counts.
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex flex-col">
            <label className="text-xs text-muted-foreground">Qdrant count</label>
            <select
              value={qdrantMethod}
              onChange={(e) => setQdrantMethod((e.target.value as "approx" | "exact") || "approx")}
              className="border rounded-md px-2 py-1 text-sm"
            >
              <option value="approx">Approx (fast)</option>
              <option value="exact">Exact (slow)</option>
            </select>
          </div>
          <Button onClick={fetchHealth} disabled={loading}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
            Refresh
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>LLM Drafting Controls</CardTitle>
            <CardDescription>
              Choose drafter/reviewer models and tweak the drafting prompt template.
            </CardDescription>
          </div>
          <div className="flex items-center gap-2">
            <Button variant="outline" size="sm" onClick={fetchLlmConfig}>
              Reload
            </Button>
            <Button onClick={saveLlmConfig} disabled={!llmConfig || llmSaving}>
              {llmSaving ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
              Save
            </Button>
          </div>
        </CardHeader>
        <CardContent className="space-y-4">
          {llmConfig ? (
            <>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className="space-y-2">
                  <p className="text-sm font-medium">Drafter model</p>
                  <div className="flex flex-wrap gap-3">
                    {(llmConfig.available_models || []).map((model) => (
                      <label key={`drafter-${model}`} className="flex items-center gap-2 text-sm">
                        <input
                          type="radio"
                          name="drafter-model"
                          value={model}
                          checked={llmConfig.drafter_model === model}
                          onChange={() => setLlmConfig({ ...llmConfig, drafter_model: model })}
                        />
                        {model}
                      </label>
                    ))}
                  </div>
                </div>
                <div className="space-y-2">
                  <p className="text-sm font-medium">Reviewer model</p>
                  <div className="flex flex-wrap gap-3">
                    {(llmConfig.available_models || []).map((model) => (
                      <label key={`reviewer-${model}`} className="flex items-center gap-2 text-sm">
                        <input
                          type="radio"
                          name="reviewer-model"
                          value={model}
                          checked={llmConfig.reviewer_model === model}
                          onChange={() => setLlmConfig({ ...llmConfig, reviewer_model: model })}
                        />
                        {model}
                      </label>
                    ))}
                  </div>
                </div>
                <div className="space-y-2">
                  <p className="text-sm font-medium">Plan model (uses Grok by default)</p>
                  <div className="flex flex-wrap gap-3">
                    {(llmConfig.available_models || []).map((model) => (
                      <label key={`plan-${model}`} className="flex items-center gap-2 text-sm">
                        <input
                          type="radio"
                          name="plan-model"
                          value={model}
                          checked={llmConfig.plan_model === model}
                          onChange={() => setLlmConfig({ ...llmConfig, plan_model: model })}
                        />
                        {model}
                      </label>
                    ))}
                  </div>
                </div>
              </div>
              <div className="space-y-2">
                <p className="text-sm font-medium">Draft prompt template</p>
                <p className="text-xs text-muted-foreground">
                  Placeholders: {"{subject}"}, {"{recipient}"}, {"{plan}"}, {"{requirements}"}, {"{sources}"}.
                </p>
                <Textarea
                  className="min-h-[180px]"
                  value={llmConfig.draft_prompt_template}
                  onChange={(e) =>
                    setLlmConfig({ ...llmConfig, draft_prompt_template: e.target.value })
                  }
                />
              </div>
              <div className="space-y-2">
                <p className="text-sm font-medium">Plan prompt template</p>
                <p className="text-xs text-muted-foreground">
                  Placeholders: {"{subject}"}, {"{recipient}"}, {"{role}"}, {"{requirements}"}, {"{sources}"}, {"{linked_letters}"}.
                </p>
                <Textarea
                  className="min-h-[140px]"
                  value={llmConfig.plan_prompt_template || ""}
                  onChange={(e) =>
                    setLlmConfig({ ...llmConfig, plan_prompt_template: e.target.value })
                  }
                />
              </div>
            </>
          ) : (
            <p className="text-sm text-muted-foreground">Loading LangGraph configuration...</p>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Bulk Repair</CardTitle>
          <CardDescription>
            Resync vectors for a scoped org/project (superadmin only). Defaults to out-of-sync docs.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-3 items-end">
            <div>
              <label className="text-xs text-muted-foreground">Org</label>
              <select
                value={orgId}
                onChange={(e) => {
                  setOrgId(e.target.value);
                  setProjectId("");
                }}
                className="w-full border rounded-md px-3 py-2 text-sm"
              >
                <option value="">Select org</option>
                {orgOptions.map((o) => (
                  <option key={o._id} value={o._id}>
                    {o.name || o._id}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-muted-foreground">Project</label>
              <select
                value={projectId}
                onChange={(e) => setProjectId(e.target.value)}
                className="w-full border rounded-md px-3 py-2 text-sm"
              >
                <option value="">Select project</option>
                {filteredProjects.map((p) => (
                  <option key={p._id} value={p._id}>
                    {p.name || p._id}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs text-muted-foreground">Limit</label>
              <input
                type="number"
                min={1}
                max={200}
                value={repairLimit}
                onChange={(e) => setRepairLimit(Math.max(1, Math.min(200, Number(e.target.value) || 1)))}
                className="w-full border rounded-md px-3 py-2 text-sm"
              />
            </div>
            <div className="flex md:justify-end">
              <Button onClick={runBulkRepair} disabled={repairing} className="w-full md:w-auto">
                {repairing ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
                Bulk Repair
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader className="flex flex-row items-center justify-between">
          <div>
            <CardTitle>Status</CardTitle>
            <CardDescription>
              Updated {health?.timestamp ? new Date(health.timestamp).toLocaleString() : "--"}
            </CardDescription>
          </div>
          <Badge variant={health?.status === "healthy" ? "primary" : "destructive"}>
            {health?.status || "unknown"}
          </Badge>
        </CardHeader>
        <CardContent className="space-y-2">
          <h4 className="font-semibold">Issues</h4>
          {health?.issues?.length ? (
            <ul className="list-disc pl-4 text-sm text-red-600">
              {health.issues.map((issue, i) => (
                <li key={i}>{issue}</li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">No issues reported.</p>
          )}
        </CardContent>
      </Card>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="h-4 w-4" /> Documents
            </CardTitle>
            <CardDescription>Total documents by backend</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span>MongoDB</span>
              <span className="font-medium">{health?.documents?.mongo ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>Qdrant</span>
              <span className="font-medium">{health?.documents?.qdrant ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>FalkorDB</span>
              <span className="font-medium">{health?.documents?.falkor ?? "—"}</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <Database className="h-4 w-4" /> Vector Stores
            </CardTitle>
            <CardDescription>Counts by backend</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span>Mongo vectors</span>
              <span className="font-medium">{health?.vector_store?.mongo_chunk_count ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>Qdrant vectors</span>
              <span className="font-medium">
                {health?.vector_store?.qdrant_chunk_count ?? "—"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>Qdrant status</span>
              <span className="font-medium">
                {health?.vector_store?.qdrant_enabled
                  ? health?.vector_store?.qdrant_available
                    ? "available"
                    : "unavailable"
                  : "disabled"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>Qdrant check</span>
              <span className="font-medium">
                {health?.vector_store?.qdrant_method
                  ? health.vector_store.qdrant_method
                  : health?.vector_store?.qdrant_exact === undefined
                    ? "unknown"
                    : health?.vector_store?.qdrant_exact
                      ? "exact"
                      : "approx"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>Qdrant latency</span>
              <span className="font-medium">
                {health?.vector_store?.qdrant_latency_ms !== undefined
                && health?.vector_store?.qdrant_latency_ms !== null
                  ? `${Math.round(health.vector_store.qdrant_latency_ms)} ms`
                  : "—"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>Qdrant retries</span>
              <span className="font-medium">{health?.vector_store?.qdrant_retries ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>Qdrant error</span>
              <span className="font-medium">{health?.vector_store?.qdrant_error ?? "—"}</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>Vector Sync</CardTitle>
            <CardDescription>Tracked documents and issues</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span>Tracked</span>
              <span className="font-medium">{health?.sync_status?.tracked_documents ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>Pending</span>
              <span className="font-medium">{health?.sync_status?.pending ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>Mismatches</span>
              <span className="font-medium">{health?.sync_status?.mismatch ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>Errors</span>
              <span className="font-medium">{health?.sync_status?.errors ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>Stale</span>
              <span className="font-medium">{health?.sync_status?.stale ?? "—"}</span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>References</CardTitle>
            <CardDescription>Backlink completeness</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span>Docs with references</span>
              <span className="font-medium">
                {health?.references?.documents_with_references ?? "—"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>Missing backlinks</span>
              <span className="font-medium">
                {health?.references?.documents_missing_backlinks ?? "—"}
              </span>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>FalkorDB</CardTitle>
            <CardDescription>Graph status</CardDescription>
          </CardHeader>
          <CardContent className="space-y-2 text-sm">
            <div className="flex justify-between">
              <span>Enabled</span>
              <span className="font-medium">{health?.falkor?.enabled ? "yes" : "no"}</span>
            </div>
            <div className="flex justify-between">
              <span>Available</span>
              <span className="font-medium">
                {health?.falkor?.available ? "available" : health?.falkor?.reason || "unavailable"}
              </span>
            </div>
            <div className="flex justify-between">
              <span>Nodes</span>
              <span className="font-medium">{health?.falkor?.nodes ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>Edges</span>
              <span className="font-medium">{health?.falkor?.edges ?? "—"}</span>
            </div>
            <div className="flex justify-between">
              <span>Avg degree</span>
              <span className="font-medium">
                {health?.falkor?.avg_degree !== undefined
                  ? health.falkor.avg_degree.toFixed(2)
                  : "—"}
              </span>
            </div>
          </CardContent>
        </Card>
      </div>

      {health?.falkor?.per_org && health.falkor.per_org.length > 0 && (
        <Card>
          <CardHeader>
            <CardTitle>FalkorDB per Org/Project</CardTitle>
            <CardDescription>Node/edge counts and average degree</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="text-left border-b">
                    <th className="py-2">Org</th>
                    <th className="py-2">Project</th>
                    <th className="py-2 text-right">Nodes</th>
                    <th className="py-2 text-right">Edges</th>
                    <th className="py-2 text-right">Avg Degree</th>
                  </tr>
                </thead>
                <tbody>
                  {health.falkor.per_org.map((row, idx) => (
                    <tr key={idx} className="border-b last:border-0">
                      <td className="py-1">{row.org_name || row.org || "—"}</td>
                      <td className="py-1">{row.project_name || row.project || "—"}</td>
                      <td className="py-1 text-right">{row.nodes}</td>
                      <td className="py-1 text-right">{row.edges}</td>
                      <td className="py-1 text-right">
                        {row.avg_degree !== undefined ? row.avg_degree.toFixed(2) : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default HealthPage;
