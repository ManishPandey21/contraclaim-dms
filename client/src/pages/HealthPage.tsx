import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertTriangle,
  Bot,
  CheckCircle2,
  Database,
  FileSearch,
  GitBranch,
  HardDrive,
  Loader2,
  RefreshCw,
  ShieldAlert,
  Wrench,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import { LANGGRAPH_ENABLED } from "@/config/features";
import { joinApiUrl } from "@/config/api";
import { toast } from "sonner";
import {
  enhancedApi as api,
  Organization,
  Project,
} from "@/services/enhanced-api";
import { authenticatedFetch } from "@/services/http";
import useRBAC from "@/hooks/useRBAC";

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

type DatabaseStatus = {
  mongo?: {
    available: boolean;
    latency_ms?: number | null;
    database_name?: string | null;
    error?: string | null;
    collections?: Record<string, number>;
  };
};

type HealthStatus = {
  status: string;
  issues: string[];
  timestamp: string;
  database?: DatabaseStatus;
  vector_store: VectorStoreStatus;
  documents?: {
    mongo?: number | null;
    qdrant?: number | null;
    falkor?: number | null;
  };
  sync_status: SyncStatus;
  references: {
    documents_with_references: number;
    documents_missing_backlinks: number;
  };
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

type VectorReconcileResult = {
  run_id?: string;
  requested: number;
  scanned: number;
  repaired: number;
  in_sync: number;
  failed: number;
  dry_run?: boolean;
  failures?: Array<Record<string, unknown>>;
  details?: Array<Record<string, unknown>>;
};

type FileReconcileResult = {
  run_id?: string;
  requested: number;
  scanned: number;
  failed: number;
  dry_run?: boolean;
  issue_counts?: Record<string, number>;
  details?: Array<Record<string, unknown>>;
};

const authHeaders = (json = false): Record<string, string> => {
  return json ? { "Content-Type": "application/json" } : {};
};

const formatNumber = (value?: number | null) =>
  typeof value === "number" ? value.toLocaleString() : "--";

const formatMs = (value?: number | null) =>
  typeof value === "number" ? `${Math.round(value)} ms` : "--";

const statusVariant = (healthy?: boolean) =>
  healthy ? "default" : "destructive";

const HealthPage: React.FC = () => {
  const [health, setHealth] = useState<HealthStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [repairing, setRepairing] = useState(false);
  const [reconciling, setReconciling] = useState(false);
  const [fileReconciling, setFileReconciling] = useState(false);
  const [orgId, setOrgId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [repairLimit, setRepairLimit] = useState<number>(10);
  const [reconcileLimit, setReconcileLimit] = useState<number>(50);
  const [fileReconcileLimit, setFileReconcileLimit] = useState<number>(100);
  const [dryRun, setDryRun] = useState(true);
  const [autoRefresh, setAutoRefresh] = useState("off");
  const [lastSuccessfulCheck, setLastSuccessfulCheck] = useState<string | null>(
    null
  );
  const [orgOptions, setOrgOptions] = useState<Organization[]>([]);
  const [projectOptions, setProjectOptions] = useState<Project[]>([]);
  const [qdrantMethod, setQdrantMethod] = useState<"approx" | "exact">(
    "approx"
  );
  const [llmConfig, setLlmConfig] = useState<LanggraphLLMConfig | null>(null);
  const [llmSaving, setLlmSaving] = useState(false);
  const [vectorResult, setVectorResult] =
    useState<VectorReconcileResult | null>(null);
  const [fileResult, setFileResult] = useState<FileReconcileResult | null>(null);

  const { roles } = useRBAC();
  const isSuperadmin = roles.includes("superadmin");

  const fetchHealth = useCallback(async () => {
    setLoading(true);
    try {
      const params = new URLSearchParams({ method: qdrantMethod });
      const res = await authenticatedFetch(joinApiUrl(`/storage-sync/status?${params}`), {
        headers: authHeaders(),
      });
      if (!res.ok) throw new Error(`Failed to fetch health (${res.status})`);
      const data = (await res.json()) as HealthStatus;
      setHealth(data);
      setLastSuccessfulCheck(new Date().toISOString());
    } catch (err) {
      console.error(err);
      toast.error((err as Error)?.message || "Failed to load health status");
    } finally {
      setLoading(false);
    }
  }, [qdrantMethod]);

  const fetchLlmConfig = useCallback(async () => {
    if (!LANGGRAPH_ENABLED) return;
    try {
      const res = await authenticatedFetch(joinApiUrl("/ai-assistant/langgraph/config"), {
        headers: authHeaders(),
      });
      if (!res.ok) {
        throw new Error(`Failed to fetch drafting engine config (${res.status})`);
      }
      setLlmConfig((await res.json()) as LanggraphLLMConfig);
    } catch (err) {
      console.error(err);
      toast.error((err as Error)?.message || "Failed to load drafting engine config");
    }
  }, []);

  const saveLlmConfig = async () => {
    if (!LANGGRAPH_ENABLED || !llmConfig) return;
    setLlmSaving(true);
    try {
      const res = await authenticatedFetch(joinApiUrl("/ai-assistant/langgraph/config"), {
        method: "PUT",
        headers: authHeaders(true),
        body: JSON.stringify({
          drafter_model: llmConfig.drafter_model,
          reviewer_model: llmConfig.reviewer_model,
          draft_prompt_template: llmConfig.draft_prompt_template,
          plan_model: llmConfig.plan_model,
          plan_prompt_template: llmConfig.plan_prompt_template,
        }),
      });
      if (!res.ok) {
        throw new Error((await res.text()) || `Save failed (${res.status})`);
      }
      setLlmConfig((await res.json()) as LanggraphLLMConfig);
      toast.success("Drafting engine configuration saved");
    } catch (err) {
      console.error(err);
      toast.error((err as Error)?.message || "Failed to save drafting engine config");
    } finally {
      setLlmSaving(false);
    }
  };

  const runBulkRepair = async () => {
    if (!orgId && !projectId) {
      toast.error("Provide org or project to scope bulk repair.");
      return;
    }
    if (!window.confirm("Run bulk vector repair for the selected scope?")) return;
    setRepairing(true);
    try {
      const res = await authenticatedFetch(joinApiUrl("/storage-sync/resync-bulk"), {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          org_id: orgId || null,
          project_id: projectId || null,
          limit: repairLimit || 10,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data?.detail || `Bulk repair failed (${res.status})`);
      toast.success(`Bulk repair complete: ${data?.succeeded ?? 0} ok, ${data?.failed ?? 0} failed.`);
      fetchHealth().catch(() => {});
    } catch (err) {
      console.error(err);
      toast.error((err as Error)?.message || "Bulk repair failed");
    } finally {
      setRepairing(false);
    }
  };

  const runVectorReconcile = async () => {
    if (!orgId && !projectId) {
      toast.error("Provide org or project to scope reconciliation.");
      return;
    }
    if (!dryRun && !window.confirm("Run vector reconciliation with repairs enabled?")) {
      return;
    }
    setReconciling(true);
    try {
      const res = await authenticatedFetch(joinApiUrl("/storage-sync/reconcile"), {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          org_id: orgId || null,
          project_id: projectId || null,
          limit: reconcileLimit,
          dry_run: dryRun,
        }),
      });
      const data = (await res.json()) as VectorReconcileResult & { detail?: string };
      if (!res.ok) throw new Error(data?.detail || `Reconciliation failed (${res.status})`);
      setVectorResult(data);
      toast.success(`Vector reconciliation scanned ${data.scanned} document(s).`);
      fetchHealth().catch(() => {});
    } catch (err) {
      console.error(err);
      toast.error((err as Error)?.message || "Vector reconciliation failed");
    } finally {
      setReconciling(false);
    }
  };

  const runFileReconcile = async () => {
    if (!orgId && !projectId) {
      toast.error("Provide org or project to scope file reconciliation.");
      return;
    }
    setFileReconciling(true);
    try {
      const res = await authenticatedFetch(joinApiUrl("/storage-sync/reconcile-files"), {
        method: "POST",
        headers: authHeaders(true),
        body: JSON.stringify({
          org_id: orgId || null,
          project_id: projectId || null,
          limit: fileReconcileLimit,
        }),
      });
      const data = (await res.json()) as FileReconcileResult & { detail?: string };
      if (!res.ok) throw new Error(data?.detail || `File reconciliation failed (${res.status})`);
      setFileResult(data);
      toast.success(`File reconciliation scanned ${data.scanned} document(s).`);
      fetchHealth().catch(() => {});
    } catch (err) {
      console.error(err);
      toast.error((err as Error)?.message || "File reconciliation failed");
    } finally {
      setFileReconciling(false);
    }
  };

  useEffect(() => {
    if (isSuperadmin) {
      fetchHealth().catch(() => {});
      if (LANGGRAPH_ENABLED) {
        fetchLlmConfig().catch(() => {});
      }
    }
  }, [fetchHealth, fetchLlmConfig, isSuperadmin]);

  useEffect(() => {
    if (!isSuperadmin || autoRefresh === "off") return;
    const ms = autoRefresh === "30s" ? 30000 : autoRefresh === "1m" ? 60000 : 300000;
    const timer = window.setInterval(() => {
      fetchHealth().catch(() => {});
    }, ms);
    return () => window.clearInterval(timer);
  }, [autoRefresh, fetchHealth, isSuperadmin]);

  useEffect(() => {
    if (!isSuperadmin) return;
    const loadOptions = async () => {
      const [orgs, projects] = await Promise.all([
        api.getOrganizations().catch(() => []),
        api.getProjects().catch(() => []),
      ]);
      setOrgOptions(orgs || []);
      setProjectOptions(projects || []);
    };
    loadOptions().catch(() => {});
  }, [isSuperadmin]);

  const filteredProjects = useMemo(() => {
    if (!orgId) return projectOptions;
    return projectOptions.filter((project) => (project as any).organization_id === orgId);
  }, [orgId, projectOptions]);

  const mongo = health?.database?.mongo;
  const hasHealthIssues = Boolean(health?.issues?.length);

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
      <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-2xl font-semibold">System Health</h1>
          <p className="text-sm text-muted-foreground">
            Superadmin operations for database, document reconciliation, vector stores, and AI drafting configuration.
          </p>
        </div>
        <div className="flex flex-wrap items-end gap-3">
          <label className="space-y-1 text-xs text-muted-foreground">
            <span>Qdrant count</span>
            <select
              value={qdrantMethod}
              onChange={(event) => setQdrantMethod(event.target.value as "approx" | "exact")}
              className="block rounded-md border bg-background px-2 py-1 text-sm text-foreground"
            >
              <option value="approx">Approx</option>
              <option value="exact">Exact</option>
            </select>
          </label>
          <label className="space-y-1 text-xs text-muted-foreground">
            <span>Auto-refresh</span>
            <select
              value={autoRefresh}
              onChange={(event) => setAutoRefresh(event.target.value)}
              className="block rounded-md border bg-background px-2 py-1 text-sm text-foreground"
            >
              <option value="off">Off</option>
              <option value="30s">30 sec</option>
              <option value="1m">1 min</option>
              <option value="5m">5 min</option>
            </select>
          </label>
          <Button onClick={fetchHealth} disabled={loading} className="gap-2">
            {loading ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
            Refresh
          </Button>
        </div>
      </div>

      <Tabs defaultValue="overview" className="space-y-4">
        <TabsList className="flex h-auto flex-wrap justify-start">
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="database">Database</TabsTrigger>
          <TabsTrigger value="reconciliation">Reconciliation</TabsTrigger>
          <TabsTrigger value="stores">Stores</TabsTrigger>
          <TabsTrigger value="repairs">Repairs</TabsTrigger>
          {LANGGRAPH_ENABLED && <TabsTrigger value="ai">AI Config</TabsTrigger>}
        </TabsList>

        <TabsContent value="overview" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-4">
            <StatusCard
              title="Overall"
              icon={<Activity className="h-4 w-4" />}
              value={health?.status || "unknown"}
              detail={`Updated ${health?.timestamp ? new Date(health.timestamp).toLocaleString() : "--"}`}
              healthy={!hasHealthIssues}
            />
            <StatusCard
              title="MongoDB"
              icon={<Database className="h-4 w-4" />}
              value={mongo?.available ? "connected" : "unavailable"}
              detail={`Latency ${formatMs(mongo?.latency_ms)}`}
              healthy={mongo?.available}
            />
            <StatusCard
              title="Qdrant"
              icon={<HardDrive className="h-4 w-4" />}
              value={
                health?.vector_store?.qdrant_enabled
                  ? health?.vector_store?.qdrant_available
                    ? "available"
                    : "unavailable"
                  : "disabled"
              }
              detail={`Latency ${formatMs(health?.vector_store?.qdrant_latency_ms)}`}
              healthy={!health?.vector_store?.qdrant_enabled || health?.vector_store?.qdrant_available}
            />
            <StatusCard
              title="FalkorDB"
              icon={<GitBranch className="h-4 w-4" />}
              value={health?.falkor?.available ? "available" : health?.falkor?.reason || "unavailable"}
              detail={`${formatNumber(health?.falkor?.nodes)} nodes`}
              healthy={!health?.falkor?.enabled || health?.falkor?.available}
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Issues</CardTitle>
              <CardDescription>
                Last successful frontend check: {lastSuccessfulCheck ? new Date(lastSuccessfulCheck).toLocaleString() : "--"}
              </CardDescription>
            </CardHeader>
            <CardContent>
              {health?.issues?.length ? (
                <ul className="space-y-2 text-sm text-red-600">
                  {health.issues.map((issue) => (
                    <li key={issue} className="flex items-center gap-2">
                      <AlertTriangle className="h-4 w-4" />
                      {issue}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="flex items-center gap-2 text-sm text-muted-foreground">
                  <CheckCircle2 className="h-4 w-4 text-green-600" />
                  No issues reported.
                </p>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="database" className="space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Database className="h-4 w-4" />
                Database Connections
              </CardTitle>
              <CardDescription>MongoDB ping, database name, and operational collection counts.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-4 md:grid-cols-2">
              <Metric label="Status" value={mongo?.available ? "connected" : "unavailable"} />
              <Metric label="Latency" value={formatMs(mongo?.latency_ms)} />
              <Metric label="Database" value={mongo?.database_name || "--"} />
              <Metric label="Error" value={mongo?.error || "--"} />
              {Object.entries(mongo?.collections || {}).map(([key, value]) => (
                <Metric key={key} label={key.replace(/_/g, " ")} value={formatNumber(value)} />
              ))}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="reconciliation" className="space-y-4">
          <ScopeControls
            orgId={orgId}
            setOrgId={setOrgId}
            projectId={projectId}
            setProjectId={setProjectId}
            orgOptions={orgOptions}
            projectOptions={filteredProjects}
          />

          <div className="grid gap-4 xl:grid-cols-2">
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <FileSearch className="h-4 w-4" />
                  Vector Reconciliation
                </CardTitle>
                <CardDescription>Compare Mongo chunks with Qdrant vectors and optionally repair mismatches.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 md:grid-cols-3">
                  <NumberInput label="Limit" value={reconcileLimit} min={1} max={200} onChange={setReconcileLimit} />
                  <label className="flex items-center gap-2 self-end text-sm">
                    <input type="checkbox" checked={dryRun} onChange={(event) => setDryRun(event.target.checked)} />
                    Dry run
                  </label>
                  <Button onClick={runVectorReconcile} disabled={reconciling} className="self-end gap-2">
                    {reconciling ? <Loader2 className="h-4 w-4 animate-spin" /> : <FileSearch className="h-4 w-4" />}
                    Run
                  </Button>
                </div>
                {vectorResult && (
                  <ResultPanel
                    title={`Run ${vectorResult.run_id || "--"}`}
                    summary={[
                      ["Scanned", vectorResult.scanned],
                      ["In sync", vectorResult.in_sync],
                      ["Repaired", vectorResult.repaired],
                      ["Failed", vectorResult.failed],
                    ]}
                    details={vectorResult.details || []}
                  />
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <HardDrive className="h-4 w-4" />
                  File / Document Reconciliation
                </CardTitle>
                <CardDescription>Find missing files, orphan chunks, no-chunk documents, stuck processing, and metadata gaps.</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="grid gap-3 md:grid-cols-2">
                  <NumberInput label="Limit" value={fileReconcileLimit} min={1} max={500} onChange={setFileReconcileLimit} />
                  <Button onClick={runFileReconcile} disabled={fileReconciling} className="self-end gap-2">
                    {fileReconciling ? <Loader2 className="h-4 w-4 animate-spin" /> : <HardDrive className="h-4 w-4" />}
                    Run
                  </Button>
                </div>
                {fileResult && (
                  <ResultPanel
                    title={`Run ${fileResult.run_id || "--"}`}
                    summary={Object.entries(fileResult.issue_counts || {})}
                    details={fileResult.details || []}
                  />
                )}
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="stores" className="space-y-4">
          <div className="grid gap-4 md:grid-cols-3">
            <Card>
              <CardHeader>
                <CardTitle>Documents</CardTitle>
                <CardDescription>Total documents by backend</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <Metric label="MongoDB" value={formatNumber(health?.documents?.mongo)} />
                <Metric label="Qdrant" value={formatNumber(health?.documents?.qdrant)} />
                <Metric label="FalkorDB" value={formatNumber(health?.documents?.falkor)} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>Vector Stores</CardTitle>
                <CardDescription>Counts and Qdrant diagnostics</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <Metric label="Mongo vectors" value={formatNumber(health?.vector_store?.mongo_chunk_count)} />
                <Metric label="Qdrant vectors" value={formatNumber(health?.vector_store?.qdrant_chunk_count)} />
                <Metric label="Qdrant check" value={health?.vector_store?.qdrant_method || "--"} />
                <Metric label="Qdrant retries" value={formatNumber(health?.vector_store?.qdrant_retries)} />
                <Metric label="Qdrant error" value={health?.vector_store?.qdrant_error || "--"} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader>
                <CardTitle>References</CardTitle>
                <CardDescription>Backlink completeness</CardDescription>
              </CardHeader>
              <CardContent className="space-y-2 text-sm">
                <Metric label="Docs with references" value={formatNumber(health?.references?.documents_with_references)} />
                <Metric label="Missing backlinks" value={formatNumber(health?.references?.documents_missing_backlinks)} />
              </CardContent>
            </Card>
          </div>

          {health?.falkor?.per_org && health.falkor.per_org.length > 0 && (
            <Card>
              <CardHeader>
                <CardTitle>FalkorDB per Org / Project</CardTitle>
                <CardDescription>Node, edge, and average degree diagnostics.</CardDescription>
              </CardHeader>
              <CardContent>
                <SimpleTable
                  rows={health.falkor.per_org.map((row) => ({
                    org: row.org_name || row.org || "--",
                    project: row.project_name || row.project || "--",
                    nodes: row.nodes,
                    edges: row.edges,
                    avg_degree: row.avg_degree?.toFixed?.(2) || "--",
                  }))}
                />
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="repairs" className="space-y-4">
          <ScopeControls
            orgId={orgId}
            setOrgId={setOrgId}
            projectId={projectId}
            setProjectId={setProjectId}
            orgOptions={orgOptions}
            projectOptions={filteredProjects}
          />
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Wrench className="h-4 w-4" />
                Bulk Vector Repair
              </CardTitle>
              <CardDescription>Resync stale or mismatched documents in the selected scope.</CardDescription>
            </CardHeader>
            <CardContent className="grid gap-3 md:grid-cols-3">
              <NumberInput label="Limit" value={repairLimit} min={1} max={200} onChange={setRepairLimit} />
              <Button onClick={runBulkRepair} disabled={repairing} className="self-end gap-2">
                {repairing ? <Loader2 className="h-4 w-4 animate-spin" /> : <Wrench className="h-4 w-4" />}
                Bulk Repair
              </Button>
            </CardContent>
          </Card>
        </TabsContent>

        {LANGGRAPH_ENABLED && (
        <TabsContent value="ai" className="space-y-4">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle className="flex items-center gap-2">
                  <Bot className="h-4 w-4" />
                  LLM Drafting Controls
                </CardTitle>
                <CardDescription>Model and prompt configuration for drafting workflows.</CardDescription>
              </div>
              <div className="flex gap-2">
                <Button variant="outline" size="sm" onClick={fetchLlmConfig}>Reload</Button>
                <Button onClick={saveLlmConfig} disabled={!llmConfig || llmSaving}>
                  {llmSaving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                  Save
                </Button>
              </div>
            </CardHeader>
            <CardContent className="space-y-4">
              {llmConfig ? (
                <>
                  <ModelPicker label="Drafter model" value={llmConfig.drafter_model} models={llmConfig.available_models} onChange={(model) => setLlmConfig({ ...llmConfig, drafter_model: model })} />
                  <ModelPicker label="Reviewer model" value={llmConfig.reviewer_model} models={llmConfig.available_models} onChange={(model) => setLlmConfig({ ...llmConfig, reviewer_model: model })} />
                  <ModelPicker label="Plan model" value={llmConfig.plan_model} models={llmConfig.available_models} onChange={(model) => setLlmConfig({ ...llmConfig, plan_model: model })} />
                  <PromptEditor label="Draft prompt template" value={llmConfig.draft_prompt_template} onChange={(value) => setLlmConfig({ ...llmConfig, draft_prompt_template: value })} />
                  <PromptEditor label="Plan prompt template" value={llmConfig.plan_prompt_template || ""} onChange={(value) => setLlmConfig({ ...llmConfig, plan_prompt_template: value })} />
                </>
              ) : (
                <p className="text-sm text-muted-foreground">Loading drafting engine configuration...</p>
              )}
            </CardContent>
          </Card>
        </TabsContent>
        )}
      </Tabs>
    </div>
  );
};

function StatusCard({
  title,
  icon,
  value,
  detail,
  healthy,
}: {
  title: string;
  icon: React.ReactNode;
  value: string;
  detail: string;
  healthy?: boolean;
}) {
  return (
    <Card>
      <CardHeader className="space-y-0 pb-2">
        <CardTitle className="flex items-center justify-between text-sm font-medium">
          <span className="flex items-center gap-2">{icon}{title}</span>
          <Badge variant={statusVariant(healthy)}>{healthy ? "OK" : "Check"}</Badge>
        </CardTitle>
      </CardHeader>
      <CardContent>
        <p className="text-2xl font-semibold">{value}</p>
        <p className="text-xs text-muted-foreground">{detail}</p>
      </CardContent>
    </Card>
  );
}

function Metric({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-4 rounded-md border px-3 py-2 text-sm">
      <span className="capitalize text-muted-foreground">{label}</span>
      <span className="text-right font-medium">{value}</span>
    </div>
  );
}

function ScopeControls({
  orgId,
  setOrgId,
  projectId,
  setProjectId,
  orgOptions,
  projectOptions,
}: {
  orgId: string;
  setOrgId: (value: string) => void;
  projectId: string;
  setProjectId: (value: string) => void;
  orgOptions: Organization[];
  projectOptions: Project[];
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>Scope</CardTitle>
        <CardDescription>Repair and reconciliation actions must be scoped to an organization or project.</CardDescription>
      </CardHeader>
      <CardContent className="grid gap-3 md:grid-cols-2">
        <label className="space-y-1 text-sm">
          <span className="font-medium">Organization</span>
          <select
            value={orgId}
            onChange={(event) => {
              setOrgId(event.target.value);
              setProjectId("");
            }}
            className="w-full rounded-md border bg-background px-3 py-2"
          >
            <option value="">Select organization</option>
            {orgOptions.map((org) => (
              <option key={org._id} value={org._id}>{org.name || org._id}</option>
            ))}
          </select>
        </label>
        <label className="space-y-1 text-sm">
          <span className="font-medium">Project</span>
          <select
            value={projectId}
            onChange={(event) => setProjectId(event.target.value)}
            className="w-full rounded-md border bg-background px-3 py-2"
          >
            <option value="">Select project</option>
            {projectOptions.map((project) => (
              <option key={project._id} value={project._id}>{project.name || project._id}</option>
            ))}
          </select>
        </label>
      </CardContent>
    </Card>
  );
}

function NumberInput({
  label,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  onChange: (value: number) => void;
}) {
  return (
    <label className="space-y-1 text-sm">
      <span className="font-medium">{label}</span>
      <input
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(event) => onChange(Math.max(min, Math.min(max, Number(event.target.value) || min)))}
        className="w-full rounded-md border bg-background px-3 py-2"
      />
    </label>
  );
}

function ResultPanel({
  title,
  summary,
  details,
}: {
  title: string;
  summary: Array<[string, unknown]>;
  details: Array<Record<string, unknown>>;
}) {
  return (
    <div className="space-y-3 rounded-md border p-3">
      <p className="text-sm font-semibold">{title}</p>
      <div className="grid gap-2 sm:grid-cols-2">
        {summary.map(([label, value]) => (
          <Metric key={label} label={label} value={String(value ?? "--")} />
        ))}
      </div>
      {details.length > 0 ? (
        <SimpleTable rows={details.slice(0, 20)} />
      ) : (
        <p className="text-sm text-muted-foreground">No detail rows returned.</p>
      )}
    </div>
  );
}

function SimpleTable({ rows }: { rows: Array<Record<string, unknown>> }) {
  const columns = Array.from(
    rows.reduce((set, row) => {
      Object.keys(row).slice(0, 8).forEach((key) => set.add(key));
      return set;
    }, new Set<string>())
  );
  if (!rows.length) return null;
  return (
    <div className="overflow-x-auto rounded-md border">
      <table className="w-full text-sm">
        <thead>
          <tr className="border-b bg-muted/40 text-left">
            {columns.map((column) => (
              <th key={column} className="px-3 py-2 font-medium capitalize">{column.replace(/_/g, " ")}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, index) => (
            <tr key={index} className="border-b last:border-0">
              {columns.map((column) => (
                <td key={column} className="max-w-[260px] truncate px-3 py-2">
                  {formatCell(row[column])}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function formatCell(value: unknown): string {
  if (value === null || value === undefined) return "--";
  if (Array.isArray(value)) return value.map(formatCell).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function ModelPicker({
  label,
  value,
  models,
  onChange,
}: {
  label: string;
  value: string;
  models: string[];
  onChange: (model: string) => void;
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{label}</p>
      <div className="flex flex-wrap gap-3">
        {(models || []).map((model) => (
          <label key={`${label}-${model}`} className="flex items-center gap-2 text-sm">
            <input type="radio" value={model} checked={value === model} onChange={() => onChange(model)} />
            {model}
          </label>
        ))}
      </div>
    </div>
  );
}

function PromptEditor({
  label,
  value,
  onChange,
}: {
  label: string;
  value: string;
  onChange: (value: string) => void;
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium">{label}</p>
      <Textarea className="min-h-[150px]" value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

export default HealthPage;
