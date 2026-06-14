import React, { useCallback, useEffect, useMemo, useState } from "react";
import {
  searchContracts,
  ContractSearchResponse,
  SearchChunk,
} from "@/services/contracts-api";
import {
  CONTRACT_CATEGORIES,
  CATEGORY_ORDER,
  ContractCategoryKey,
} from "@/config/contract-categories";
import { api } from "@/services/api";
import PageHeader from "@/components/ui/PageHeader";
import {
  Card,
  CardHeader,
  CardTitle,
  CardDescription,
  CardContent,
  CardFooter,
} from "@/components/ui/card";
import Badge from "@/components/ui/badge";
import {
  Search,
  Building2,
  FolderOpen,
  FileText,
  Loader2,
  BookmarkPlus,
  History,
  Trash2,
  Play,
  Sparkles,
  Filter,
  X,
  ChevronLeft,
  ChevronRight,
  ChevronDown,
  ChevronUp,
} from "lucide-react";

type Organization = { id: string; name: string; shortName?: string | null };
type Project = { _id: string; name: string; organization_id: string };
type DocumentOption = {
  _id: string; // upload_id from /contracts/list
  filename: string;
};

type SavedSearch = {
  id: string;
  name: string;
  query: string;
  categories: ContractCategoryKey[];
  summarize: boolean;
  organization_id?: string;
  project_id?: string;
  document_id?: string;
  createdAt: string;
};

type HistoryEntry = {
  query: string;
  categories: ContractCategoryKey[];
  summarize: boolean;
  organization_id?: string;
  project_id?: string;
  document_id?: string;
  ts: string;
};

type ClauseResult = {
  id: string;
  clauseNumber: string;
  clauseTitle: string;
  clauseType: string;
  clauseLevel: number;
  parentClauseNumber?: string | null;
  clauseStartPosition?: number;
  clauseEndPosition?: number;
  chunkCount: number;
  isCompleteClause: boolean;
  sourceFilename?: string | null;
  score?: number;
  chunks: SearchChunk[];
};

const groupChunksByClause = (chunks: SearchChunk[]): ClauseResult[] => {
  if (!chunks?.length) return [];

  const groups = new Map<string, SearchChunk[]>();

  chunks.forEach((chunk) => {
    const docKey =
      chunk.document_id ??
      chunk.source_file ??
      chunk.source_filename ??
      "document";

    const clauseNumber = chunk.clause_number ?? `clause-${chunk.chunk_index}`;

    const startPosition =
      typeof chunk.clause_start_position === "number"
        ? chunk.clause_start_position
        : typeof chunk.page === "number"
        ? chunk.page * 10_000 + (chunk.chunk_index ?? 0)
        : chunk.chunk_index ?? 0;

    const key = `${docKey}::${clauseNumber}::${startPosition}`;

    const existing = groups.get(key);
    if (existing) {
      existing.push(chunk);
    } else {
      groups.set(key, [chunk]);
    }
  });

  const results: ClauseResult[] = [];

  groups.forEach((groupChunks, key) => {
    if (!groupChunks.length) return;

    const sortedChunks = [...groupChunks].sort((a, b) => {
      const idxA =
        typeof a.chunk_index === "number" ? a.chunk_index : Number.MAX_SAFE_INTEGER;
      const idxB =
        typeof b.chunk_index === "number" ? b.chunk_index : Number.MAX_SAFE_INTEGER;
      return idxA - idxB;
    });

    const first = sortedChunks[0];

    const startPositions = sortedChunks
      .map((item) => item.clause_start_position)
      .filter((value): value is number => typeof value === "number");

    const endPositions = sortedChunks
      .map((item) => item.clause_end_position)
      .filter((value): value is number => typeof value === "number");

    const bestScore = sortedChunks.reduce<number | null>((acc, current) => {
      if (typeof current.score === "number") {
        return acc === null ? current.score : Math.max(acc, current.score);
      }
      return acc;
    }, null);

    results.push({
      id: key,
      clauseNumber: first.clause_number ?? key.split("::")[1] ?? "N/A",
      clauseTitle: first.clause_title ?? "Untitled clause",
      clauseType: (first.clause_type ?? "clause").toLowerCase(),
      clauseLevel: first.clause_level ?? 1,
      parentClauseNumber: first.parent_clause_number ?? null,
      clauseStartPosition: startPositions.length
        ? Math.min(...startPositions)
        : undefined,
      clauseEndPosition: endPositions.length ? Math.max(...endPositions) : undefined,
      chunkCount: sortedChunks.length,
      isCompleteClause:
        sortedChunks.length === 1 && (first.is_complete_clause ?? true),
      sourceFilename: first.source_filename ?? first.source_file ?? null,
      score: bestScore ?? undefined,
      chunks: sortedChunks,
    });
  });

  return results.sort((a, b) => {
    if (
      typeof a.clauseStartPosition === "number" &&
      typeof b.clauseStartPosition === "number"
    ) {
      return a.clauseStartPosition - b.clauseStartPosition;
    }
    if (typeof a.clauseStartPosition === "number") return -1;
    if (typeof b.clauseStartPosition === "number") return 1;
    return a.clauseNumber.localeCompare(b.clauseNumber, undefined, {
      numeric: true,
      sensitivity: "base",
    });
  });
};

type ClauseResultCardProps = {
  clause: ClauseResult;
  renderHighlighted: (
    text: string,
    offsets?: SearchChunk["offsets"],
    fallbackTextForHighlight?: string
  ) => React.ReactNode;
  expandedQuery: string;
};

const ClauseResultCard: React.FC<ClauseResultCardProps> = ({
  clause,
  renderHighlighted,
  expandedQuery,
}) => {
  const [expanded, setExpanded] = useState(false);

  const firstChunk = clause.chunks[0];
  const visibleChunks = expanded ? clause.chunks : clause.chunks.slice(0, 1);
  const truncatedInCollapsed =
    !expanded && firstChunk && firstChunk.text.length > 2000;
  const hasMoreContent =
    clause.chunkCount > 1 || truncatedInCollapsed || !clause.isCompleteClause;

  const typeBadgeLabel =
    clause.clauseType.charAt(0).toUpperCase() + clause.clauseType.slice(1);

  return (
    <div className="border border-gray-200 rounded-lg p-4 hover:shadow-sm transition-shadow">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-2 flex-1 min-w-[220px]">
          <div className="flex flex-wrap items-center gap-2">
            <FileText className="h-4 w-4 text-gray-400" />
            <span className="font-medium text-gray-900">
              {clause.sourceFilename || "Unknown file"}
            </span>
            <Badge variant="outline" className="text-xs capitalize">
              {typeBadgeLabel}
            </Badge>
            {!clause.isCompleteClause && (
              <Badge
                variant="outline"
                className="text-xs text-amber-700 border-amber-300"
              >
                Split clause
              </Badge>
            )}
            {typeof clause.score === "number" && (
              <Badge variant="outline" className="text-xs">
                Score: {clause.score.toFixed(3)}
              </Badge>
            )}
          </div>
          <div className="text-xs text-gray-600">
            <span className="font-semibold text-gray-700">
              Clause {clause.clauseNumber}
            </span>
            {clause.clauseTitle ? ` — ${clause.clauseTitle}` : ""}
          </div>
          <div className="flex flex-wrap gap-3 text-[11px] text-gray-500">
            {clause.parentClauseNumber && (
              <span>Parent: {clause.parentClauseNumber}</span>
            )}
            {clause.clauseLevel ? <span>Level {clause.clauseLevel}</span> : null}
            {typeof clause.clauseStartPosition === "number" &&
              typeof clause.clauseEndPosition === "number" && (
                <span>
                  Chars {clause.clauseStartPosition} - {clause.clauseEndPosition}
                </span>
              )}
            {clause.chunkCount > 1 && <span>{clause.chunkCount} segments</span>}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 text-[11px] text-gray-500 min-w-[160px]">
          {firstChunk?.document_id && (
            <span className="font-mono">{firstChunk.document_id}</span>
          )}
          {(firstChunk?.page || firstChunk?.section) && (
            <span>
              {firstChunk?.page ? `Page ${firstChunk.page}` : ""}
              {firstChunk?.page && firstChunk?.section ? " • " : ""}
              {firstChunk?.section ? `Section: ${firstChunk.section}` : ""}
            </span>
          )}
        </div>
      </div>
      <div className="mt-3 space-y-4">
        {visibleChunks.map((chunk, idx) => {
          const chunkIndex =
            typeof chunk.chunk_index === "number"
              ? chunk.chunk_index + 1
              : idx + 1;
          const truncated = !expanded && chunk.text.length > 2000;
          const displayText = truncated
            ? chunk.text.slice(0, 2000)
            : chunk.text;
          return (
            <div key={`${clause.id}-${chunk.chunk_index}-${idx}`}>
              {clause.chunkCount > 1 && (
                <div className="text-xs font-medium text-gray-500 mb-1">
                  Segment {chunkIndex}
                </div>
              )}
              <div className="text-sm text-gray-700 whitespace-pre-wrap leading-relaxed">
                {renderHighlighted(displayText, chunk.offsets, expandedQuery)}
                {truncated && (
                  <span className="text-gray-500"> (truncated)</span>
                )}
              </div>
            </div>
          );
        })}
      </div>
      {hasMoreContent && (
        <button
          type="button"
          onClick={() => setExpanded((prev) => !prev)}
          className="mt-4 text-sm text-blue-600 hover:text-blue-700 flex items-center gap-1"
        >
          {expanded ? (
            <>
              Collapse clause <ChevronUp className="h-4 w-4" />
            </>
          ) : (
            <>
              Show full clause
              {clause.chunkCount > 1 ? ` (${clause.chunkCount} parts)` : ""}
              <ChevronDown className="h-4 w-4" />
            </>
          )}
        </button>
      )}
    </div>
  );
};

const SAVED_SEARCHES_KEY = "contracts_saved_searches";
const HISTORY_KEY = "contracts_search_history";
const HISTORY_LIMIT = 20;

const ContractsSearchPage: React.FC = () => {
  // Organization / Project fetching and selection
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [documents, setDocuments] = useState<DocumentOption[]>([]);
  const [orgLoading, setOrgLoading] = useState<boolean>(false);
  const [projLoading, setProjLoading] = useState<boolean>(false);
  const [docsLoading, setDocsLoading] = useState<boolean>(false);
  const [docsError, setDocsError] = useState<string | null>(null);
  const [orgId, setOrgId] = useState<string>(
    () => window.localStorage.getItem("org_id") || ""
  );
  const [projId, setProjId] = useState<string>(
    () => window.localStorage.getItem("proj_id") || ""
  );
  const [docId, setDocId] = useState<string>(
    () => window.localStorage.getItem("doc_id") || ""
  );

  // Highlight helper using backend-provided offsets (fallback to regex on provided text)
  const renderHighlighted = useCallback(
    (
      text: string,
      offsets?: { start: number; end: number }[] | null,
      fallbackTextForHighlight?: string
    ) => {
      if (!text) return null;

      if (offsets && offsets.length > 0) {
        const parts: React.ReactNode[] = [];
        let cursor = 0;
        const normalized = offsets
          .filter((o) => Number.isFinite(o.start) && Number.isFinite(o.end))
          .sort((a, b) => a.start - b.start);

        normalized.forEach(({ start, end }, idx) => {
          const s = Math.max(0, Math.min(text.length, start));
          const e = Math.max(0, Math.min(text.length, end));
          if (cursor < s)
            parts.push(
              <span key={`t-${idx}-${cursor}`}>{text.slice(cursor, s)}</span>
            );
          parts.push(
            <mark key={`m-${idx}-${s}-${e}`} className="bg-yellow-200">
              {text.slice(s, e)}
            </mark>
          );
          cursor = e;
        });
        if (cursor < text.length)
          parts.push(<span key={`t-end-${cursor}`}>{text.slice(cursor)}</span>);
        return <>{parts}</>;
      }

      // Fallback: naive regex highlight for query words (2+ chars)
      const words = Array.from(
        new Set(
          (fallbackTextForHighlight || "")
            .toLowerCase()
            .match(/[A-Za-z0-9]{2,}/g) || []
        )
      );
      if (!words.length) return <>{text}</>;
      const escape = (w: string) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
      const regex = new RegExp(`(${words.map(escape).join("|")})`, "gi");
      const pieces = text.split(regex);

      return (
        <>
          {pieces.map((p, i) =>
            regex.test(p) ? (
              <mark key={i} className="bg-yellow-200">
                {p}
              </mark>
            ) : (
              <span key={i}>{p}</span>
            )
          )}
        </>
      );
    },
    []
  );

  // Query, categories and options
  const [query, setQuery] = useState<string>("");
  const [summarize, setSummarize] = useState<boolean>(true);
  const [selectedCats, setSelectedCats] = useState<ContractCategoryKey[]>([]);
  const [searching, setSearching] = useState<boolean>(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchResult, setSearchResult] =
    useState<ContractSearchResponse | null>(null);
  const groupedClauseResults = useMemo(
    () => groupChunksByClause(searchResult?.results ?? []),
    [searchResult?.results]
  );
  const chunkCount = searchResult?.results?.length ?? 0;
  const clauseCount = groupedClauseResults.length;
  // Pagination
  const [page, setPage] = useState<number>(1);
  const [limit, setLimit] = useState<number>(12);

  // Saved searches and history
  const [savedSearches, setSavedSearches] = useState<SavedSearch[]>([]);
  const [history, setHistory] = useState<HistoryEntry[]>([]);

  // Load organizations on mount
  useEffect(() => {
    const fetchOrgs = async () => {
      setOrgLoading(true);
      try {
        const { data } = await api.get("/organizations", {
          params: { skip: 0, limit: 200 },
        });
        const records = Array.isArray(data)
          ? data
          : Array.isArray(data?.organizations)
          ? data.organizations
          : [];
        const list: Organization[] = records.map((o: any) => ({
          id: o.id ?? o._id ?? "",
          name: o.name ?? "",
          shortName: o.shortName ?? null,
        }));
        setOrganizations(list);
      } catch (e) {
        console.error("Failed to fetch organizations", e);
      } finally {
        setOrgLoading(false);
      }
    };
    fetchOrgs();
  }, []);

  // Load projects when org changes
  useEffect(() => {
    const fetchProjects = async () => {
      if (!orgId) {
        setProjects([]);
        return;
      }
      setProjLoading(true);
      try {
        const { data } = await api.get("/projects", {
          params: { organization_id: orgId },
        });
        const list: Project[] = (data || []).map((p: any) => ({
          _id: p._id ?? p.id ?? "",
          name: p.name ?? "",
          organization_id: p.organization_id ?? "",
        }));
        setProjects(list);
      } catch (e) {
        console.error("Failed to fetch projects", e);
      } finally {
        setProjLoading(false);
      }
    };
    fetchProjects();
  }, [orgId]);

  // Load contract files (uploads) when org or project changes
  useEffect(() => {
    const fetchDocuments = async () => {
      if (!orgId) {
        setDocuments([]);
        setDocsError(null);
        return;
      }
      setDocsLoading(true);
      setDocsError(null);
      try {
        const { data } = await api.get("/contracts/list", {
          params: {
            organization_id: orgId,
            project_id: projId || undefined,
            limit: 200,
            skip: 0,
          },
        });
        const uploads = (data?.uploads || []) as any[];
        const list: DocumentOption[] = uploads.map((u) => ({
          _id: u.upload_id ?? "",
          filename: u.filename ?? "file",
        }));
        setDocuments(list);
      } catch (e: any) {
        console.error("Failed to fetch contract uploads", e);
        setDocuments([]);
        setDocsError(e?.message || "Failed to load files");
      } finally {
        setDocsLoading(false);
      }
    };
    fetchDocuments();
  }, [orgId, projId]);

  // Persist org/proj/doc
  useEffect(() => {
    if (orgId) window.localStorage.setItem("org_id", orgId);
    if (projId) window.localStorage.setItem("proj_id", projId);
    if (docId) window.localStorage.setItem("doc_id", docId);
  }, [orgId, projId, docId]);

  // Load saved searches and history
  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(SAVED_SEARCHES_KEY);
      if (raw) setSavedSearches(JSON.parse(raw));
    } catch {
      setSavedSearches([]);
    }
    try {
      const rawH = window.localStorage.getItem(HISTORY_KEY);
      if (rawH) setHistory(JSON.parse(rawH));
    } catch {
      setHistory([]);
    }
  }, []);

  // Derived: expanded query with category keywords
  const expandedQuery = useMemo(() => {
    if (selectedCats.length === 0) return query;
    const keywords = selectedCats.flatMap(
      (k) => CONTRACT_CATEGORIES[k].keywords
    );
    const unique = Array.from(new Set(keywords));
    return [query, unique.join(" ")].filter(Boolean).join(" ");
  }, [query, selectedCats]);

  const onToggleCategory = useCallback((key: ContractCategoryKey) => {
    setSelectedCats((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }, []);

  const pushHistory = useCallback(
    (entry: HistoryEntry) => {
      const next = [entry, ...history].slice(0, HISTORY_LIMIT);
      setHistory(next);
      try {
        window.localStorage.setItem(HISTORY_KEY, JSON.stringify(next));
      } catch {}
    },
    [history]
  );

  const doSearch = useCallback(
    async (q: string, p: number = 1) => {
      if (!q.trim()) return;
      setSearching(true);
      setSearchError(null);
      setSearchResult(null);
      try {
        const resp = await searchContracts({
          query: q,
          organization_id: orgId || undefined,
          project_id: projId || undefined,
          document_id: docId || undefined,
          skip: (Math.max(1, p) - 1) * limit,
          limit,
          top_docs: 6,
          chunks_per_doc: 2,
          summarize,
          tags: selectedCats.length ? selectedCats : undefined,
        });
        setSearchResult(resp);
      } catch (e: any) {
        console.error(e);
        setSearchError(e?.message || "Search failed");
      } finally {
        setSearching(false);
      }
    },
    [orgId, projId, docId, limit, summarize, selectedCats]
  );

  const handleSearch = useCallback(async () => {
    if (!expandedQuery.trim()) {
      alert("Enter a search query or select categories");
      return;
    }
    setPage(1);
    await doSearch(expandedQuery, 1);

    // Record history
    const entry: HistoryEntry = {
      query,
      categories: selectedCats,
      summarize,
      organization_id: orgId || undefined,
      project_id: projId || undefined,
      document_id: docId || undefined,
      ts: new Date().toISOString(),
    };
    pushHistory(entry);
  }, [
    expandedQuery,
    query,
    orgId,
    projId,
    docId,
    summarize,
    selectedCats,
    pushHistory,
    doSearch,
  ]);

  const saveCurrentSearch = useCallback(() => {
    const name = window.prompt("Enter a name for this search:");
    if (!name) return;
    const item: SavedSearch = {
      id: crypto.randomUUID
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random()}`,
      name,
      query,
      categories: selectedCats,
      summarize,
      organization_id: orgId || undefined,
      project_id: projId || undefined,
      document_id: docId || undefined,
      createdAt: new Date().toISOString(),
    };
    const next = [item, ...savedSearches];
    setSavedSearches(next);
    try {
      window.localStorage.setItem(SAVED_SEARCHES_KEY, JSON.stringify(next));
    } catch {}
  }, [query, selectedCats, summarize, orgId, projId, docId, savedSearches]);

  const loadSavedSearch = useCallback((s: SavedSearch) => {
    setQuery(s.query);
    setSelectedCats(s.categories || []);
    setSummarize(!!s.summarize);
    if (s.organization_id) setOrgId(s.organization_id);
    if (s.project_id) setProjId(s.project_id);
    if (s.document_id) setDocId(s.document_id);
  }, []);

  const deleteSavedSearch = useCallback(
    (id: string) => {
      const next = savedSearches.filter((s) => s.id !== id);
      setSavedSearches(next);
      try {
        window.localStorage.setItem(SAVED_SEARCHES_KEY, JSON.stringify(next));
      } catch {}
    },
    [savedSearches]
  );

  const loadHistory = useCallback((h: HistoryEntry) => {
    setQuery(h.query);
    setSelectedCats(h.categories || []);
    setSummarize(!!h.summarize);
    if (h.organization_id) setOrgId(h.organization_id);
    if (h.project_id) setProjId(h.project_id);
    if (h.document_id) setDocId(h.document_id);
  }, []);

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6">
      <PageHeader
        icon={<Search className="h-6 w-6" />}
        title="Search Contract Clauses"
        description="Search for specific clauses across all uploaded contract documents"
      />

      {/* Organization & Project Selection */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Building2 className="h-5 w-5" />
            Organization & Project
          </CardTitle>
          <CardDescription>
            Select organization and project to scope your search
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">
                Organization
              </label>
              <select
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                value={orgId}
                onChange={(e) => {
                  setOrgId(e.target.value);
                  setProjId("");
                  setDocId("");
                }}
                disabled={orgLoading}
              >
                <option value="">All Organizations</option>
                {organizations.map((o) => (
                  <option key={o.id} value={o.id}>
                    {o.name}
                  </option>
                ))}
              </select>
              {orgLoading && (
                <p className="text-xs text-gray-500 flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Loading organizations...
                </p>
              )}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">
                Project (optional)
              </label>
              <select
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
                value={projId}
                onChange={(e) => {
                  setProjId(e.target.value);
                  setDocId("");
                }}
                disabled={!orgId || projLoading}
              >
                <option value="">All Projects</option>
                {projects.map((p) => (
                  <option key={p._id} value={p._id}>
                    {p.name}
                  </option>
                ))}
              </select>
              {projLoading && (
                <p className="text-xs text-gray-500 flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Loading projects...
                </p>
              )}
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Categories Selection */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Filter className="h-5 w-5" />
            Categories
          </CardTitle>
          <CardDescription>
            Select categories to bias the search toward related clauses. Their
            keywords will be appended to your query and tags will filter on the
            backend.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            <div className="flex flex-wrap gap-2">
              {CATEGORY_ORDER.map((key) => {
                const cat = CONTRACT_CATEGORIES[key];
                const isSelected = selectedCats.includes(key);
                return (
                  <button
                    key={key}
                    onClick={() => onToggleCategory(key)}
                    className={`
                      px-3 py-2 rounded-md text-sm font-medium transition-colors
                      ${
                        isSelected
                          ? "bg-blue-100 text-blue-700 border border-blue-200"
                          : "bg-gray-100 text-gray-700 border border-gray-200 hover:bg-gray-200"
                      }
                    `}
                    title={cat.description}
                  >
                    {key}
                  </button>
                );
              })}
            </div>

            {selectedCats.length > 0 && (
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <p className="text-sm font-medium text-gray-700">
                    Selected Categories ({selectedCats.length})
                  </p>
                  <button
                    onClick={() => setSelectedCats([])}
                    className="text-sm text-red-600 hover:text-red-700 flex items-center gap-1"
                  >
                    <X className="h-3 w-3" />
                    Clear all
                  </button>
                </div>
                <div className="flex flex-wrap gap-1">
                  {selectedCats.map((key) => (
                    <Badge key={key} variant="primary" className="text-xs">
                      {key}
                    </Badge>
                  ))}
                </div>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Search Query */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Search className="h-5 w-5" />
            Search Query
          </CardTitle>
          <CardDescription>
            Enter keywords to find relevant clauses in your contract documents
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="space-y-4">
            {/* File Selection Dropdown */}
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">
                Target file
              </label>
              <select
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
                value={docId}
                onChange={(e) => {
                  setDocId(e.target.value);
                }}
                disabled={docsLoading || !orgId}
              >
                <option value="">All Files</option>
                {documents.map((doc) => (
                  <option key={doc._id} value={doc._id}>
                    {doc.filename}
                  </option>
                ))}
              </select>
              {docsLoading && (
                <p className="text-xs text-gray-500 flex items-center gap-1">
                  <Loader2 className="h-3 w-3 animate-spin" />
                  Loading files...
                </p>
              )}
              {!docsLoading && docsError && (
                <p className="text-xs text-red-600">{docsError}</p>
              )}
              {!docsLoading && !docsError && documents.length === 0 && (
                <p className="text-xs text-gray-500">
                  No files found for the selected scope.
                </p>
              )}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">
                Search query
              </label>
              <div className="relative">
                <input
                  type="text"
                  className="w-full pl-10 pr-4 py-3 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                  placeholder='e.g., "termination clause", "payment terms", "liability"'
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !searching) {
                      handleSearch();
                    }
                  }}
                />
                <Search className="absolute left-3 top-1/2 transform -translate-y-1/2 h-4 w-4 text-gray-400" />
              </div>
              {selectedCats.length > 0 && (
                <div className="text-xs text-gray-600">
                  <p className="font-medium">
                    Expanded with category keywords:
                  </p>
                  <div className="mt-1 p-2 bg-gray-50 border rounded text-xs font-mono break-words">
                    {expandedQuery}
                  </div>
                </div>
              )}
            </div>

            <div className="flex items-center justify-between">
              <label className="flex items-center gap-2">
                <input
                  type="checkbox"
                  checked={summarize}
                  onChange={(e) => setSummarize(e.target.checked)}
                  className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                />
                <span className="text-sm text-gray-700 flex items-center gap-1">
                  <Sparkles className="h-4 w-4" />
                  Summarize matches with AI
                </span>
              </label>

              <div className="flex items-center gap-2">
                <button
                  onClick={saveCurrentSearch}
                  className="px-4 py-2 text-sm font-medium text-green-700 bg-green-50 border border-green-200 rounded-md hover:bg-green-100 transition-colors flex items-center gap-1"
                >
                  <BookmarkPlus className="h-4 w-4" />
                  Save Search
                </button>
                <button
                  onClick={handleSearch}
                  disabled={searching || !expandedQuery.trim()}
                  className={`
                    px-6 py-2 text-sm font-medium rounded-md transition-colors flex items-center gap-2
                    ${
                      searching || !expandedQuery.trim()
                        ? "bg-gray-300 text-gray-500 cursor-not-allowed"
                        : "bg-blue-600 hover:bg-blue-700 text-white"
                    }
                  `}
                >
                  {searching ? (
                    <>
                      <Loader2 className="h-4 w-4 animate-spin" />
                      Searching...
                    </>
                  ) : (
                    <>
                      <Search className="h-4 w-4" />
                      Search
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Search Results */}
      {searchError && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Search Results
            </CardTitle>
            <CardDescription>Error</CardDescription>
          </CardHeader>
          <CardContent>
            <div className="text-sm text-red-600">{searchError}</div>
          </CardContent>
        </Card>
      )}

      {searchResult && (
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <FileText className="h-5 w-5" />
              Search Results
            </CardTitle>
            <CardDescription>
              Found {clauseCount} clause
              {clauseCount !== 1 ? "s" : ""} ({chunkCount} matching segment
              {chunkCount !== 1 ? "s" : ""}) on page {page}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {searchResult.summary && (
              <div className="mb-6 p-4 bg-amber-50 border border-amber-200 rounded-lg">
                <div className="flex items-center gap-2 mb-2">
                  <Sparkles className="h-4 w-4 text-amber-600" />
                  <h4 className="font-medium text-amber-800">
                    {searchResult.ai_summary_title || "AI Summary"}
                  </h4>
                </div>
                <div className="text-sm text-amber-700 whitespace-pre-wrap">
                  {searchResult.summary}
                </div>
              </div>
            )}

            <div className="space-y-4">
              {groupedClauseResults.map((clause) => (
                <ClauseResultCard
                  key={clause.id}
                  clause={clause}
                  renderHighlighted={renderHighlighted}
                  expandedQuery={expandedQuery}
                />
              ))}
            </div>

            {chunkCount === 0 && !searching && (
              <div className="text-center py-12">
                <Search className="mx-auto h-12 w-12 text-gray-400" />
                <h3 className="mt-4 text-lg font-medium text-gray-900">
                  No clauses found
                </h3>
                <p className="mt-2 text-sm text-gray-500">
                  Try adjusting your search query or filters to find relevant
                  clauses.
                </p>
              </div>
            )}

            {/* Pagination */}
            <div className="mt-6 flex items-center justify-between">
              <button
                className="px-3 py-2 border rounded-md text-sm flex items-center gap-1 disabled:opacity-50"
                onClick={() => {
                  const next = Math.max(1, page - 1);
                  setPage(next);
                  doSearch(expandedQuery, next);
                }}
                disabled={page <= 1 || searching}
              >
                <ChevronLeft className="h-4 w-4" />
                Prev
              </button>
              <span className="text-sm text-gray-600">Page {page}</span>
              {(() => {
                const hasMore =
                  !searching && (searchResult?.results?.length ?? 0) >= limit;
                return (
                  <button
                    className="px-3 py-2 border rounded-md text-sm flex items-center gap-1 disabled:opacity-50"
                    onClick={() => {
                      if (!hasMore) return;
                      const next = page + 1;
                      setPage(next);
                      doSearch(expandedQuery, next);
                    }}
                    disabled={!hasMore || searching}
                    title={hasMore ? "Next page" : "No more items"}
                  >
                    {hasMore ? (
                      <>
                        Next
                        <ChevronRight className="h-4 w-4" />
                      </>
                    ) : (
                      <>No more items</>
                    )}
                  </button>
                );
              })()}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Saved Searches and History */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* Saved Searches */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <BookmarkPlus className="h-5 w-5" />
              Saved Searches
            </CardTitle>
            <CardDescription>
              Quick access to your frequently used searches
            </CardDescription>
          </CardHeader>
          <CardContent>
            {savedSearches.length === 0 ? (
              <div className="text-center py-8">
                <BookmarkPlus className="mx-auto h-8 w-8 text-gray-400" />
                <p className="mt-2 text-sm text-gray-500">
                  No saved searches yet
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {savedSearches.map((search) => (
                  <div
                    key={search.id}
                    className="border border-gray-200 rounded-lg p-3"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1 min-w-0">
                        <h4 className="text-sm font-medium text-gray-900 truncate">
                          {search.name}
                        </h4>
                        <p className="text-xs text-gray-500 mt-1 truncate">
                          {search.query} • {search.categories.length} categories
                          {search.document_id && " • Specific file"}
                        </p>
                        <p className="text-xs text-gray-400 mt-1">
                          {new Date(search.createdAt).toLocaleDateString()}
                        </p>
                      </div>
                      <div className="flex items-center gap-1 ml-2">
                        <button
                          onClick={() => loadSavedSearch(search)}
                          className="p-1 text-blue-600 hover:text-blue-700 transition-colors"
                          title="Load search"
                        >
                          <Play className="h-3 w-3" />
                        </button>
                        <button
                          onClick={() => deleteSavedSearch(search.id)}
                          className="p-1 text-red-600 hover:text-red-700 transition-colors"
                          title="Delete search"
                        >
                          <Trash2 className="h-3 w-3" />
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* Recent History */}
        <Card>
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <History className="h-5 w-5" />
              Recent History
            </CardTitle>
            <CardDescription>Your recent search queries</CardDescription>
          </CardHeader>
          <CardContent>
            {history.length === 0 ? (
              <div className="text-center py-8">
                <History className="mx-auto h-8 w-8 text-gray-400" />
                <p className="mt-2 text-sm text-gray-500">
                  No search history yet
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                {history.map((entry, idx) => (
                  <div
                    key={`${idx}-${entry.ts}`}
                    className="border border-gray-200 rounded-lg p-3"
                  >
                    <div className="flex items-start justify-between">
                      <div className="flex-1 min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">
                          {entry.query || "Category search"}
                        </p>
                        <p className="text-xs text-gray-500 mt-1 truncate">
                          {entry.categories.length} categories selected
                          {entry.document_id && " • Specific file"}
                        </p>
                        <p className="text-xs text-gray-400 mt-1">
                          {new Date(entry.ts).toLocaleString()}
                        </p>
                      </div>
                      <button
                        onClick={() => loadHistory(entry)}
                        className="p-1 text-blue-600 hover:text-blue-700 transition-colors ml-2"
                        title="Load search"
                      >
                        <Play className="h-3 w-3" />
                      </button>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>

      <div className="text-xs text-gray-400 text-center">
        Search operates over vectorized contract chunks (uploadType="contract")
        limited to your org/project scope. Categories apply keyword biasing and
        tag-based filtering.
      </div>
    </div>
  );
};

export default ContractsSearchPage;
export { ContractsSearchPage };
