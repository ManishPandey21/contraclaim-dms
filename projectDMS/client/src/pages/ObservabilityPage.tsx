import React, { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { Activity, AlertTriangle, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import useRBAC from "@/hooks/useRBAC";
import { RagRunLog, getObservabilityLogs } from "@/services/observability-api";

const RUN_TYPES = ["", "search_qdrant", "search_mongo", "rag_qdrant", "rag_mongo", "agent"];
const fmtTime = (d?: string | null) => (d ? new Date(d).toLocaleString() : "—");
const fmtMs = (n?: number | null) => (typeof n === "number" ? `${Math.round(n)} ms` : "—");

const ObservabilityPage = () => {
  const { roles } = useRBAC();
  const isSuperadmin = roles.includes("superadmin");
  const [logs, setLogs] = useState<RagRunLog[]>([]);
  const [loading, setLoading] = useState(false);
  const [runType, setRunType] = useState("");
  const [orgId, setOrgId] = useState("");
  const [projectId, setProjectId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const params: Record<string, string> = {};
      if (runType) params.run_type = runType;
      if (orgId) params.org_id = orgId;
      if (projectId) params.project_id = projectId;
      setLogs(await getObservabilityLogs(params));
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || "Failed to load observability logs");
    } finally {
      setLoading(false);
    }
  }, [runType, orgId, projectId]);

  useEffect(() => {
    void load();
  }, [load]);

  const errorCount = logs.filter((l) => l.error).length;
  const avgLatency =
    logs.length > 0
      ? Math.round(
          logs.reduce((s, l) => s + (typeof l.latency_ms === "number" ? l.latency_ms : 0), 0) /
            logs.length,
        )
      : 0;

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Activity className="h-6 w-6 text-blue-600" />
          <div>
            <h1 className="text-2xl font-bold">Retrieval Observability</h1>
            <p className="text-sm text-muted-foreground">
              Recent RAG / retrieval run logs — latency, result counts and errors.
            </p>
          </div>
        </div>
        <Button variant="outline" onClick={load} disabled={loading}>
          {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
          Refresh
        </Button>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        <Card><CardHeader className="pb-2"><CardTitle className="text-2xl">{logs.length}</CardTitle></CardHeader><CardContent className="pt-0 text-xs text-muted-foreground">Runs (latest 200)</CardContent></Card>
        <Card><CardHeader className="pb-2"><CardTitle className="text-2xl text-amber-600">{fmtMs(avgLatency)}</CardTitle></CardHeader><CardContent className="pt-0 text-xs text-muted-foreground">Avg latency</CardContent></Card>
        <Card><CardHeader className="pb-2"><CardTitle className={`text-2xl ${errorCount ? "text-red-600" : "text-green-600"}`}>{errorCount}</CardTitle></CardHeader><CardContent className="pt-0 text-xs text-muted-foreground">Errors</CardContent></Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Run Logs</CardTitle>
          <div className="flex flex-wrap gap-3 pt-3">
            <Select value={runType || "all"} onValueChange={(v) => setRunType(v === "all" ? "" : v)}>
              <SelectTrigger className="w-48"><SelectValue placeholder="Run type" /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All run types</SelectItem>
                {RUN_TYPES.filter(Boolean).map((t) => (
                  <SelectItem key={t} value={t}>{t}</SelectItem>
                ))}
              </SelectContent>
            </Select>
            {!isSuperadmin && (
              <>
                <Input placeholder="org_id" value={orgId} onChange={(e) => setOrgId(e.target.value)} className="w-40" />
                <Input placeholder="project_id" value={projectId} onChange={(e) => setProjectId(e.target.value)} className="w-40" />
              </>
            )}
          </div>
          {!isSuperadmin && (
            <p className="pt-2 text-xs text-muted-foreground">
              Non-superadmins must provide org_id and project_id to scope the query.
            </p>
          )}
        </CardHeader>
        <CardContent>
          {loading ? (
            <div className="flex items-center justify-center py-12 text-muted-foreground">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" /> Loading…
            </div>
          ) : logs.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
              <AlertTriangle className="mb-2 h-8 w-8" /> No run logs.
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Time</TableHead>
                  <TableHead>Run type</TableHead>
                  <TableHead>Strategy</TableHead>
                  <TableHead>Query</TableHead>
                  <TableHead>Latency</TableHead>
                  <TableHead>Results</TableHead>
                  <TableHead>Status</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {logs.map((l, i) => (
                  <TableRow key={l.run_id || i}>
                    <TableCell className="whitespace-nowrap text-xs">{fmtTime(l.created_at)}</TableCell>
                    <TableCell className="font-mono text-xs">{l.run_type || "—"}</TableCell>
                    <TableCell>{l.strategy || "—"}</TableCell>
                    <TableCell className="max-w-xs truncate" title={l.query || ""}>{l.query || "—"}</TableCell>
                    <TableCell>{fmtMs(l.latency_ms)}</TableCell>
                    <TableCell>{l.extra?.counts?.results ?? "—"}</TableCell>
                    <TableCell>
                      {l.error ? (
                        <Badge variant="secondary" className="bg-red-100 text-red-800">error</Badge>
                      ) : (
                        <Badge variant="secondary" className="bg-green-100 text-green-800">ok</Badge>
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
};

export default ObservabilityPage;
