import React, { useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { Bot, Loader2, Search, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { enhancedApi as api } from "@/services/enhanced-api";
import {
  RagResponse,
  RetrievalCitation,
  SearchResponse,
  ragQuery,
  semanticSearch,
} from "@/services/retrieval-api";

interface Org { _id: string; name: string }
interface Project { _id: string; name: string; organization_id: string }

const CitationCard = ({ c }: { c: RetrievalCitation }) => (
  <div className="rounded-md border bg-white p-3 text-sm">
    <div className="mb-1 flex items-center justify-between gap-2">
      <span className="font-medium">
        {c.document_title || c.file_name || c.letter_no || c.document_id}
      </span>
      {c.score != null && (
        <Badge variant="secondary" className="bg-sky-100 text-sky-800">
          {c.score.toFixed(3)}
        </Badge>
      )}
    </div>
    <p className="whitespace-pre-wrap text-muted-foreground">{c.snippet}</p>
    {c.page != null && (
      <div className="mt-1 text-xs text-muted-foreground">Page {c.page}</div>
    )}
  </div>
);

const RetrievalConsolePage = () => {
  const [orgs, setOrgs] = useState<Org[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [orgId, setOrgId] = useState("");
  const [projectId, setProjectId] = useState("");
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"rag" | "search">("rag");
  const [strategy, setStrategy] = useState<"vanilla" | "hyde" | "rag_fusion">("vanilla");
  const [backend, setBackend] = useState<"auto" | "qdrant" | "mongo">("auto");
  const [running, setRunning] = useState(false);
  const [rag, setRag] = useState<RagResponse | null>(null);
  const [search, setSearch] = useState<SearchResponse | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [o, p] = await Promise.all([
          api.getOrganizations().catch(() => []),
          api.getProjects().catch(() => []),
        ]);
        setOrgs(o as Org[]);
        setProjects(p as Project[]);
        if ((o as Org[]).length) setOrgId((o as Org[])[0]._id);
      } catch {
        /* selection optional */
      }
    })();
  }, []);

  const projectsForOrg = useMemo(
    () => projects.filter((p) => !orgId || p.organization_id === orgId),
    [projects, orgId],
  );

  const run = async () => {
    if (!query.trim()) {
      toast.error("Enter a question or search query.");
      return;
    }
    if (!orgId || !projectId) {
      toast.error("Select an organization and project to scope retrieval.");
      return;
    }
    setRunning(true);
    setRag(null);
    setSearch(null);
    try {
      const payload = {
        query: query.trim(),
        filters: { org_id: orgId, project_id: projectId },
        limit: 8,
        strategy,
        backend,
      };
      if (mode === "rag") {
        setRag(await ragQuery(payload));
      } else {
        setSearch(await semanticSearch(payload));
      }
    } catch (e: any) {
      toast.error(e?.response?.data?.detail || e?.message || "Retrieval failed");
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="container mx-auto space-y-6 p-6">
      <div className="flex items-center gap-2">
        <Bot className="h-6 w-6 text-blue-600" />
        <div>
          <h1 className="text-2xl font-bold">Retrieval Console</h1>
          <p className="text-sm text-muted-foreground">
            Ask grounded questions (RAG) or run semantic search over your indexed
            documents. Results are scoped to the selected org/project.
          </p>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Query</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="grid gap-1.5">
              <Label>Organization</Label>
              <Select value={orgId} onValueChange={(v) => { setOrgId(v); setProjectId(""); }}>
                <SelectTrigger><SelectValue placeholder="Select organization" /></SelectTrigger>
                <SelectContent>
                  {orgs.map((o) => <SelectItem key={o._id} value={o._id}>{o.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="grid gap-1.5">
              <Label>Project</Label>
              <Select value={projectId} onValueChange={setProjectId}>
                <SelectTrigger><SelectValue placeholder="Select project" /></SelectTrigger>
                <SelectContent>
                  {projectsForOrg.map((p) => <SelectItem key={p._id} value={p._id}>{p.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>

          <div className="grid gap-1.5">
            <Label>Question / search</Label>
            <Textarea
              rows={3}
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="e.g. What does the contract say about delay damages?"
            />
          </div>

          <div className="flex flex-wrap items-end gap-3">
            <div className="grid gap-1.5">
              <Label>Mode</Label>
              <Select value={mode} onValueChange={(v) => setMode(v as "rag" | "search")}>
                <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="rag">RAG answer</SelectItem>
                  <SelectItem value="search">Semantic search</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {mode === "rag" && (
              <div className="grid gap-1.5">
                <Label>Strategy</Label>
                <Select value={strategy} onValueChange={(v) => setStrategy(v as any)}>
                  <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="vanilla">Vanilla</SelectItem>
                    <SelectItem value="hyde">HyDE</SelectItem>
                    <SelectItem value="rag_fusion">RAG Fusion</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            )}
            <div className="grid gap-1.5">
              <Label>Backend</Label>
              <Select value={backend} onValueChange={(v) => setBackend(v as any)}>
                <SelectTrigger className="w-32"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="auto">Auto</SelectItem>
                  <SelectItem value="qdrant">Qdrant</SelectItem>
                  <SelectItem value="mongo">Mongo</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <Button onClick={run} disabled={running}>
              {running ? (
                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              ) : mode === "rag" ? (
                <Sparkles className="mr-2 h-4 w-4" />
              ) : (
                <Search className="mr-2 h-4 w-4" />
              )}
              {running ? "Running…" : "Run"}
            </Button>
          </div>
        </CardContent>
      </Card>

      {rag && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>Answer</span>
              <Badge variant="secondary">{rag.strategy_used}</Badge>
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <p className="whitespace-pre-wrap">{rag.answer}</p>
            {rag.citations?.length > 0 && (
              <div className="space-y-2">
                <Label className="text-xs text-muted-foreground">
                  Citations ({rag.citations.length})
                </Label>
                {rag.citations.map((c, i) => <CitationCard key={`${c.chunk_id}-${i}`} c={c} />)}
              </div>
            )}
            {rag.timings && Object.keys(rag.timings).length > 0 && (
              <div className="text-xs text-muted-foreground">
                {Object.entries(rag.timings).map(([k, v]) => `${k}: ${v}ms`).join("  ·  ")}
              </div>
            )}
          </CardContent>
        </Card>
      )}

      {search && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>Search results ({search.results.length})</span>
              {search.backend_used && <Badge variant="secondary">{search.backend_used}</Badge>}
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2">
            {search.results.length === 0 ? (
              <p className="text-muted-foreground">No matching passages.</p>
            ) : (
              search.results.map((c, i) => <CitationCard key={`${c.chunk_id}-${i}`} c={c} />)
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
};

export default RetrievalConsolePage;
