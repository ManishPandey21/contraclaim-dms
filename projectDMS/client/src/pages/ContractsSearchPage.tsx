import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  searchContracts,
  ContractSearchResponse,
  SearchChunk,
  ContractSource,
} from "@/services/contracts-api";
import { extractErrorMessage } from "@/lib/error-logger";
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
import { toast } from "sonner";
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
  Eye,
} from "lucide-react";

type Organization = { id: string; name: string; shortName?: string | null };
type Project = { _id: string; name: string; organization_id: string };
type DocumentOption = {
  id: string;
  filename: string;
};

type SavedSearch = {
  id: string;
  name: string;
  query: string;
  categories: ContractCategoryKey[];
  summarize: boolean;
  exact_phrase?: boolean;
  clause_number?: string;
  clause_title?: string;
  section_heading?: string;
  clause_tag?: string;
  page_from?: string;
  page_to?: string;
  organization_id?: string;
  project_id?: string;
  document_id?: string;
  createdAt: string;
};

type HistoryEntry = {
  query: string;
  categories: ContractCategoryKey[];
  summarize: boolean;
  exact_phrase?: boolean;
  clause_number?: string;
  clause_title?: string;
  section_heading?: string;
  clause_tag?: string;
  page_from?: string;
  page_to?: string;
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
  fileName?: string | null;
  sectionHeading?: string | null;
  clauseTags?: string[] | null;
  pageNumbers?: number[] | null;
  pageNumber?: number | null;
  score?: number;
  chunks: SearchChunk[];
};

const groupChunksByClause = (chunks: SearchChunk[]): ClauseResult[] => {
  if (!chunks?.length) return [];

  const groups = new Map<string, SearchChunk[]>();

  chunks.forEach((chunk) => {
    const fileName =
      chunk.file_name ??
      chunk.source_filename ??
      (chunk as any).filename ??
      "document";
    const docKey =
      chunk.upload_id ??
      chunk.document_id ??
      fileName;

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
        typeof a.chunk_index === "number"
          ? a.chunk_index
          : Number.MAX_SAFE_INTEGER;
      const idxB =
        typeof b.chunk_index === "number"
          ? b.chunk_index
          : Number.MAX_SAFE_INTEGER;
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

    const fileName =
      first.file_name ??
      first.source_filename ??
      (first as any).filename ??
      "document";

    const pageNumbers =
      first.page_numbers && first.page_numbers.length > 0
        ? first.page_numbers
        : typeof first.page === "number"
        ? [first.page]
        : typeof (first as any).page_number === "number"
        ? [(first as any).page_number]
        : undefined;

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
      clauseEndPosition: endPositions.length
        ? Math.max(...endPositions)
        : undefined,
      chunkCount: sortedChunks.length,
      isCompleteClause:
        sortedChunks.length === 1 && (first.is_complete_clause ?? true),
      sourceFilename: fileName,
      fileName,
      sectionHeading:
        first.section_heading ?? first.section ?? first.clause_title ?? null,
      clauseTags: first.clause_tags ?? null,
      pageNumbers: pageNumbers ?? null,
      pageNumber: pageNumbers?.[0] ?? null,
      score: bestScore ?? undefined,
      chunks: sortedChunks,
    });
  });

  return results.sort((a, b) => {
    const scoreA = typeof a.score === "number" ? a.score : -Infinity;
    const scoreB = typeof b.score === "number" ? b.score : -Infinity;
    if (scoreA !== scoreB) {
      return scoreB - scoreA; // higher score (match %) first
    }

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
  onViewSource?: (documentId: string) => void;
};

const ClauseResultCard: React.FC<ClauseResultCardProps> = ({
  clause,
  renderHighlighted,
  expandedQuery,
  onViewSource,
}) => {
  const [expanded, setExpanded] = useState(false);
  const sourceDocumentId = clause.chunks.find((c) => c.document_id)?.document_id ?? null;

  const firstChunk = clause.chunks[0];
  const fullClauseText =
    clause.chunks
      ?.map((chunk) => chunk.text)
      .filter(Boolean)
      .join("\n\n")
      .trim() ||
    firstChunk?.text ||
    "";
  const previewLimit = 900;
  const shouldTruncate = !expanded && fullClauseText.length > previewLimit;
  const displayText = shouldTruncate
    ? `${fullClauseText.slice(0, previewLimit)}...`
    : fullClauseText;
  const showToggle =
    fullClauseText.length > previewLimit || clause.chunkCount > 1;

  const typeBadgeLabel =
    clause.clauseType.charAt(0).toUpperCase() + clause.clauseType.slice(1);

  return (
    <Card className="border border-gray-200 shadow-sm">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="flex-1 min-w-[220px] space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm px-2 py-1 bg-gray-100 text-gray-700 rounded">
                {clause.clauseNumber || "N/A"}
              </span>
              <CardTitle className="text-lg leading-tight">
                {clause.clauseTitle || "Untitled clause"}
              </CardTitle>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="outline" className="text-xs capitalize">
                {typeBadgeLabel}
              </Badge>
              <Badge
                variant={clause.isCompleteClause ? "success" : "warning"}
                className={`text-xs ${
                  clause.isCompleteClause
                    ? "bg-emerald-600 hover:bg-emerald-600 text-white"
                    : "text-amber-800 border-amber-200 bg-amber-50"
                }`}
              >
                {clause.isCompleteClause ? "Complete clause" : "Split clause"}
              </Badge>
              {clause.chunkCount > 1 && (
                <Badge variant="outline" className="text-xs">
                  {clause.chunkCount} segments
                </Badge>
              )}
            </div>
            <div className="text-sm text-gray-500 flex flex-wrap gap-4">
              {clause.sourceFilename && (
                <span className="flex items-center gap-1">
                  <FileText className="h-4 w-4 text-gray-400" />
                  {clause.sourceFilename}
                </span>
              )}
              {clause.pageNumbers && clause.pageNumbers.length > 0 && (
                <span className="flex items-center gap-1">
                  Pages: {clause.pageNumbers.join(", ")}
                </span>
              )}
              {clause.sectionHeading &&
                clause.sectionHeading !== clause.clauseTitle && (
                  <span>Section: {clause.sectionHeading}</span>
                )}
              {clause.clauseTags && clause.clauseTags.length > 0 && (
                <span className="flex items-center gap-1">
                  Tags: {clause.clauseTags.join(", ")}
                </span>
              )}
              {clause.parentClauseNumber && (
                <span>Parent: {clause.parentClauseNumber}</span>
              )}
            </div>
          </div>
          <div className="flex items-start gap-3">
            {typeof clause.score === "number" && (
              <div className="text-right min-w-[90px]">
                <p className="text-xs text-gray-500">Matches</p>
                <p className="text-lg font-semibold text-gray-900">
                  {clause.score}
                </p>
              </div>
            )}
            {sourceDocumentId && onViewSource && (
              <button
                type="button"
                onClick={() => onViewSource(sourceDocumentId)}
                className="shrink-0 inline-flex items-center gap-1 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-blue-600 hover:bg-blue-50"
                title="Open this document in the Contract Viewer"
              >
                <Eye className="h-3.5 w-3.5" />
                View
              </button>
            )}
          </div>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {fullClauseText && (
          <div>
            <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
              Clause Content
            </p>
            <div className="bg-gray-50 border rounded-lg p-4 text-sm text-gray-800 whitespace-pre-wrap leading-relaxed max-h-[32rem] overflow-auto">
              {renderHighlighted(displayText, undefined, expandedQuery)}
            </div>
            {showToggle && (
              <button
                type="button"
                onClick={() => setExpanded((prev) => !prev)}
                className="mt-3 text-sm text-blue-600 hover:text-blue-700 flex items-center gap-1"
              >
                {expanded ? (
                  <>
                    Show Less <ChevronUp className="h-4 w-4" />
                  </>
                ) : (
                  <>
                    Show Complete Clause
                    {clause.chunkCount > 1
                      ? ` (${clause.chunkCount} parts)`
                      : ""}
                    <ChevronDown className="h-4 w-4" />
                  </>
                )}
              </button>
            )}
          </div>
        )}

        <div>
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-2">
            Metadata
          </p>
          <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 text-sm text-gray-600">
            <div>
              <p className="font-medium text-gray-800">Hierarchy level</p>
              <p>Level {clause.clauseLevel ?? "N/A"}</p>
            </div>
            <div>
              <p className="font-medium text-gray-800">Character range</p>
              {typeof clause.clauseStartPosition === "number" &&
              typeof clause.clauseEndPosition === "number" ? (
                <p>
                  {clause.clauseStartPosition} - {clause.clauseEndPosition}
                </p>
              ) : (
                <p>Not available</p>
              )}
            </div>
            <div>
              <p className="font-medium text-gray-800">Segments</p>
              <p>
                {clause.chunkCount ?? 0}{" "}
                {clause.chunkCount === 1 ? "segment" : "segments"}
              </p>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};
const HISTORY_LIMIT = 20;
const SEARCH_TIMEOUT_MS = 30_000;

const ContractsSearchPage: React.FC = () => {
  const navigate = useNavigate();
  // Organization / Project fetching and selection
  const [organizations, setOrganizations] = useState<Organization[]>([]);
  const [projects, setProjects] = useState<Project[]>([]);
  const [documents, setDocuments] = useState<DocumentOption[]>([]);
  const [orgLoading, setOrgLoading] = useState<boolean>(false);
  const [projLoading, setProjLoading] = useState<boolean>(false);
  const [docsLoading, setDocsLoading] = useState<boolean>(false);
  const [docsError, setDocsError] = useState<string | null>(null);
  const [orgId, setOrgId] = useState<string>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("organization_id") || window.localStorage.getItem("org_id") || "";
  });
  const [projId, setProjId] = useState<string>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("project_id") || window.localStorage.getItem("proj_id") || "";
  });
  const [docId, setDocId] = useState<string>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("document_id") || window.localStorage.getItem("doc_id") || "";
  });
  const searchAbortRef = useRef<AbortController | null>(null);

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
      const lowerWords = new Set(words.map((word) => word.toLowerCase()));
      const pieces = text.split(/([A-Za-z0-9]+)/g);

      return (
        <>
          {pieces.map((p, i) =>
            lowerWords.has(p.toLowerCase()) ? (
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
  const [query, setQuery] = useState<string>(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get("query") || "";
  });
  const [summarize, setSummarize] = useState<boolean>(true);
  const [selectedCats, setSelectedCats] = useState<ContractCategoryKey[]>([]);
  const [exactPhrase, setExactPhrase] = useState<boolean>(false);
  const [clauseNumber, setClauseNumber] = useState<string>("");
  const [clauseTitle, setClauseTitle] = useState<string>("");
  const [sectionHeading, setSectionHeading] = useState<string>("");
  const [clauseTag, setClauseTag] = useState<string>("");
  const [pageFrom, setPageFrom] = useState<string>("");
  const [pageTo, setPageTo] = useState<string>("");
  const [searching, setSearching] = useState<boolean>(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [searchResult, setSearchResult] =
    useState<ContractSearchResponse | null>(null);
  const autoSearchTriggered = useRef(false);
  const groupedClauseResults = useMemo(
    () => groupChunksByClause(searchResult?.results ?? []),
    [searchResult?.results]
  );
  const chunkCount = searchResult?.results?.length ?? 0;
  const clauseCount = groupedClauseResults.length;
  const typedSearchResult =
    (searchResult as ContractSearchResponse & {
      aisummary?: string | null;
      ai_summary?: string | null;
      aiSummary?: string | null;
      aiSummaryTitle?: string | null;
    }) || null;
  const aiSummary =
    typedSearchResult?.aisummary ??
    typedSearchResult?.ai_summary ??
    typedSearchResult?.aiSummary ??
    typedSearchResult?.summary ??
    null;
  const aiSummaryTitle =
    typedSearchResult?.ai_summary_title ??
    typedSearchResult?.aiSummaryTitle ??
    "AI Generated Summary";
  const relevantSources = useMemo<ContractSource[]>(() => {
    if (Array.isArray(searchResult?.sources)) {
      return (searchResult?.sources as ContractSource[]).map((s) => ({
        ...s,
        clause_tags: s.clause_tags ?? [],
        page_numbers: s.page_numbers ?? [],
      }));
    }
    const seen = new Set<string>();
    const collected: ContractSource[] = [];
    groupedClauseResults.forEach((clause) => {
      const key = `${clause.fileName || clause.sourceFilename || "src"}::${clause.clauseNumber}`;
      if (seen.has(key)) return;
      seen.add(key);
      collected.push({
        upload_id:
          clause.chunks[0]?.upload_id ??
          clause.chunks[0]?.document_id ??
          undefined,
        document_id: clause.chunks[0]?.document_id ?? undefined,
        file_name: clause.fileName || clause.sourceFilename || null,
        clause_number: clause.clauseNumber,
        clause_title: clause.clauseTitle,
        section_heading: clause.sectionHeading || null,
        clause_tags: clause.clauseTags || [],
        page_numbers: clause.pageNumbers || [],
        page_number: clause.pageNumber || undefined,
        page: clause.pageNumber || undefined,
      });
    });
    return collected;
  }, [searchResult?.sources, groupedClauseResults]);
  // Pagination
  const [page, setPage] = useState<number>(1);
  const limit = 12;

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
        const list: DocumentOption[] = uploads
          .filter((u) => (u?.status || "").toLowerCase() === "completed")
          .map((u) => ({
            id: String(u.document_id ?? ""),
            filename: u.filename ?? "file",
          }))
          .filter((doc) => doc.id);
        setDocuments(list);
      } catch (e) {
        console.error("Failed to fetch contract uploads", e);
        setDocuments([]);
        setDocsError(extractErrorMessage(e, "Failed to load files."));
      } finally {
        setDocsLoading(false);
      }
    };
    fetchDocuments();
  }, [orgId, projId]);

  // Persist org/proj/doc
  useEffect(() => {
    if (orgId) {
      window.localStorage.setItem("org_id", orgId);
    } else {
      window.localStorage.removeItem("org_id");
    }
    if (projId) {
      window.localStorage.setItem("proj_id", projId);
    } else {
      window.localStorage.removeItem("proj_id");
    }
    if (docId) {
      window.localStorage.setItem("doc_id", docId);
    } else {
      window.localStorage.removeItem("doc_id");
    }
  }, [orgId, projId, docId]);

  useEffect(() => {
    if (docId && !documents.some((doc) => doc.id === docId)) {
      setDocId("");
    }
  }, [docId, documents]);

  useEffect(() => {
    setSearchResult(null);
    setSearchError(null);
    setPage(1);
  }, [orgId, projId, docId]);

  // Derived: preserve the user's query and send category keywords separately
  const expandedQuery = useMemo(() => {
    const baseQuery = query.trim();
    return exactPhrase &&
      /\s/.test(baseQuery) &&
      !(baseQuery.startsWith('"') && baseQuery.endsWith('"'))
        ? `"${baseQuery}"`
        : baseQuery;
  }, [query, exactPhrase]);

  const categoryTerms = useMemo(() => {
    const keywords = selectedCats.flatMap(
      (k) => CONTRACT_CATEGORIES[k].keywords
    );
    return Array.from(new Set(keywords));
  }, [selectedCats]);

  const onToggleCategory = useCallback((key: ContractCategoryKey) => {
    setSelectedCats((prev) =>
      prev.includes(key) ? prev.filter((k) => k !== key) : [...prev, key]
    );
  }, []);

  const pushHistory = useCallback((entry: HistoryEntry) => {
    setHistory((prev) => [entry, ...prev].slice(0, HISTORY_LIMIT));
  }, []);

  const doSearch = useCallback(
    async (q: string, p: number = 1) => {
      if (!q.trim()) return;
      searchAbortRef.current?.abort();
      const controller = new AbortController();
      let timedOut = false;
      const timeoutId = window.setTimeout(() => {
        timedOut = true;
        controller.abort();
      }, SEARCH_TIMEOUT_MS);
      searchAbortRef.current = controller;
      setSearching(true);
      setSearchError(null);
      setSearchResult(null);
      try {
        const pageSize = limit;
        const skip = (p - 1) * pageSize;

        const resp = await searchContracts({
          query: q,
          organization_id: orgId || undefined,
          project_id: projId || undefined,
          document_id: docId || undefined,
          skip,
          limit: pageSize,
          top_docs: 6,
          chunks_per_doc: 2,
          summarize,
          exact_phrase: exactPhrase,
          clause_number: clauseNumber.trim() || undefined,
          clause_title: clauseTitle.trim() || undefined,
          section_heading: sectionHeading.trim() || undefined,
          clause_tags: clauseTag.trim() ? [clauseTag.trim()] : undefined,
          category_terms: categoryTerms,
          page_from: pageFrom.trim() ? Number(pageFrom) : undefined,
          page_to: pageTo.trim() ? Number(pageTo) : undefined,
          signal: controller.signal,
        });

        setSearchResult(resp);
        setPage(resp.current_page ?? p);
      } catch (e: any) {
        if ((e?.name === "CanceledError" || e?.code === "ERR_CANCELED") && !timedOut) {
          return;
        }
        console.error(e);
        setSearchError(
          timedOut
            ? "Search timed out. Narrow the scope or try again."
            : extractErrorMessage(e, "Search failed.")
        );
      } finally {
        window.clearTimeout(timeoutId);
        if (searchAbortRef.current === controller) {
          searchAbortRef.current = null;
          setSearching(false);
        }
      }
    },
    [
      orgId,
      projId,
      docId,
      limit,
      summarize,
      exactPhrase,
      clauseNumber,
      clauseTitle,
      sectionHeading,
      clauseTag,
      categoryTerms,
      pageFrom,
      pageTo,
    ]
  );

  useEffect(() => {
    return () => {
      searchAbortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    if (autoSearchTriggered.current) return;
    const params = new URLSearchParams(window.location.search);
    const queryParam = params.get("query");
    if (!queryParam || !orgId) return;
    autoSearchTriggered.current = true;
    void doSearch(expandedQuery, 1);
  }, [doSearch, expandedQuery, orgId]);

  const handleSearch = useCallback(async () => {
    if (!orgId) {
      toast.warning("Select an organization before searching.");
      return;
    }
    if (!expandedQuery.trim()) {
      toast.warning("Enter a search query or select categories.");
      return;
    }
    setPage(1);
    await doSearch(expandedQuery, 1);

    // Record history
    const entry: HistoryEntry = {
      query,
      categories: selectedCats,
      summarize,
      exact_phrase: exactPhrase,
      clause_number: clauseNumber,
      clause_title: clauseTitle,
      section_heading: sectionHeading,
      clause_tag: clauseTag,
      page_from: pageFrom,
      page_to: pageTo,
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
    exactPhrase,
    clauseNumber,
    clauseTitle,
    sectionHeading,
    clauseTag,
    pageFrom,
    pageTo,
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
      exact_phrase: exactPhrase,
      clause_number: clauseNumber,
      clause_title: clauseTitle,
      section_heading: sectionHeading,
      clause_tag: clauseTag,
      page_from: pageFrom,
      page_to: pageTo,
      organization_id: orgId || undefined,
      project_id: projId || undefined,
      document_id: docId || undefined,
      createdAt: new Date().toISOString(),
    };
    setSavedSearches((prev) => [item, ...prev]);
  }, [query, selectedCats, summarize, exactPhrase, clauseNumber, clauseTitle, sectionHeading, clauseTag, pageFrom, pageTo, orgId, projId, docId]);

  const loadSavedSearch = useCallback((s: SavedSearch) => {
    setQuery(s.query);
    setSelectedCats(s.categories || []);
    setSummarize(!!s.summarize);
    setExactPhrase(!!s.exact_phrase);
    setClauseNumber(s.clause_number || "");
    setClauseTitle(s.clause_title || "");
    setSectionHeading(s.section_heading || "");
    setClauseTag(s.clause_tag || "");
    setPageFrom(s.page_from || "");
    setPageTo(s.page_to || "");
    if (s.organization_id) setOrgId(s.organization_id);
    if (s.project_id) setProjId(s.project_id);
    if (s.document_id) setDocId(s.document_id);
  }, []);

  const deleteSavedSearch = useCallback(
    (id: string) => {
      setSavedSearches((prev) => prev.filter((s) => s.id !== id));
    },
    []
  );

  const loadHistory = useCallback((h: HistoryEntry) => {
    setQuery(h.query);
    setSelectedCats(h.categories || []);
    setSummarize(!!h.summarize);
    setExactPhrase(!!h.exact_phrase);
    setClauseNumber(h.clause_number || "");
    setClauseTitle(h.clause_title || "");
    setSectionHeading(h.section_heading || "");
    setClauseTag(h.clause_tag || "");
    setPageFrom(h.page_from || "");
    setPageTo(h.page_to || "");
    if (h.organization_id) setOrgId(h.organization_id);
    if (h.project_id) setProjId(h.project_id);
    if (h.document_id) setDocId(h.document_id);
  }, []);

  return (
    <div className="max-w-7xl mx-auto p-6 space-y-6">
      <PageHeader
        icon={<Search className="h-6 w-6" />}
        title="Search Contract Clauses"
        description="Search completed contract clauses within your authorized organization and project scope."
      />

      {/* Organization & Project Selection */}
      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <Building2 className="h-5 w-5" />
            Organization & Project
          </CardTitle>
          <CardDescription>
            Select an organization, then optionally narrow to a project or specific completed contract.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">
                Organization *
              </label>
              <select
                data-testid="contract-search-org-select"
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                value={orgId}
                onChange={(e) => {
                  setOrgId(e.target.value);
                  setProjId("");
                  setDocId("");
                }}
                disabled={orgLoading}
              >
                <option value="">Select organization</option>
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
                data-testid="contract-search-project-select"
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
                value={projId}
                onChange={(e) => {
                  setProjId(e.target.value);
                  setDocId("");
                }}
                disabled={!orgId || projLoading}
              >
                <option value="">
                  {orgId ? "All accessible projects" : "Select organization first"}
                </option>
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
            Select categories to bias reranking toward related legal concepts.
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
                data-testid="contract-search-file-select"
                className="w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500 disabled:bg-gray-50 disabled:text-gray-500"
                value={docId}
                onChange={(e) => {
                  setDocId(e.target.value);
                }}
                disabled={docsLoading || !orgId}
              >
                <option value="">All completed files in scope</option>
                {documents.map((doc) => (
                  <option key={doc.id} value={doc.id}>
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
                  No completed contract files found for the selected scope.
                </p>
              )}
            </div>

            <div className="space-y-2">
              <label className="text-sm font-medium text-gray-700">
                Search query
              </label>
              <div className="relative">
                <input
                  data-testid="contract-search-query-input"
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
              {(selectedCats.length > 0 || exactPhrase) && (
                <div className="text-xs text-gray-600">
                  <p className="font-medium">
                    Search modifiers:
                  </p>
                  <div className="mt-1 flex flex-wrap gap-1">
                    {exactPhrase && (
                      <Badge variant="neutral" className="text-xs">
                        Exact phrase
                      </Badge>
                    )}
                    {selectedCats.map((key) => (
                      <Badge key={key} variant="neutral" className="text-xs">
                        {key}
                      </Badge>
                    ))}
                  </div>
                </div>
              )}
            </div>

            <div className="space-y-3 rounded-md border border-gray-200 bg-gray-50 p-3">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="text-sm font-medium text-gray-700">
                    Legal filters
                  </p>
                  <p className="text-xs text-gray-500">
                    Narrow by contract structure before semantic reranking.
                  </p>
                </div>
                <label className="flex items-center gap-2 text-sm text-gray-700">
                  <input
                    type="checkbox"
                    checked={exactPhrase}
                    onChange={(e) => setExactPhrase(e.target.checked)}
                    className="rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                  />
                  Exact phrase
                </label>
              </div>

              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-gray-600">
                    Clause number
                  </label>
                  <input
                    type="text"
                    value={clauseNumber}
                    onChange={(e) => setClauseNumber(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    placeholder="e.g., 5.1"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-gray-600">
                    Clause title
                  </label>
                  <input
                    type="text"
                    value={clauseTitle}
                    onChange={(e) => setClauseTitle(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    placeholder="e.g., Termination"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-gray-600">
                    Section heading
                  </label>
                  <input
                    type="text"
                    value={sectionHeading}
                    onChange={(e) => setSectionHeading(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    placeholder="e.g., General Conditions"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-gray-600">
                    Clause tag
                  </label>
                  <input
                    type="text"
                    value={clauseTag}
                    onChange={(e) => setClauseTag(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    placeholder="e.g., risk"
                  />
                </div>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <label className="text-xs font-medium text-gray-600">
                    Page from
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={pageFrom}
                    onChange={(e) => setPageFrom(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    placeholder="1"
                  />
                </div>
                <div className="space-y-1">
                  <label className="text-xs font-medium text-gray-600">
                    Page to
                  </label>
                  <input
                    type="number"
                    min={1}
                    value={pageTo}
                    onChange={(e) => setPageTo(e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-md text-sm shadow-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-blue-500"
                    placeholder="10"
                  />
                </div>
              </div>
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
                  data-testid="contract-search-submit"
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
            <CardDescription className="text-sm text-gray-600">
              {searchResult.total_count ?? clauseCount} matched clause
              {(searchResult.total_count ?? clauseCount) === 1 ? "" : "s"} | {chunkCount} matching
              segment{chunkCount === 1 ? "" : "s"} on this page | Page {page}
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-6">
            {aiSummary && (
              <div className="border border-blue-200 bg-blue-50 rounded-lg p-4 shadow-sm">
                <div className="flex items-center gap-2 text-blue-900 font-semibold">
                  <Sparkles className="h-4 w-4" />
                  {aiSummaryTitle || "AI Generated Summary"}
                </div>
                <p className="mt-2 text-sm text-blue-900 whitespace-pre-wrap leading-relaxed">
                  {aiSummary}
                </p>
              </div>
            )}

            <div className="flex flex-wrap gap-4 text-sm text-gray-600">
              <span>
                <span className="font-semibold text-gray-900">
                  {clauseCount}
                </span>{" "}
                matched clause{clauseCount === 1 ? "" : "s"}
              </span>
              <span>
                <span className="font-semibold text-gray-900">
                  {chunkCount}
                </span>{" "}
                relevant segment{chunkCount === 1 ? "" : "s"}
              </span>
            </div>

            {relevantSources.length > 0 && (
              <div className="border border-gray-200 rounded-lg p-3 bg-gray-50">
                <p className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-2">
                  Relevant Sources
                </p>
                <ul className="divide-y divide-gray-200">
                  {relevantSources.slice(0, 8).map((src, idx) => (
                    <li
                      key={`${src.file_name || src.document_id || idx}-${idx}`}
                      className="py-2 flex flex-wrap items-start justify-between gap-3"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium text-gray-900 truncate">
                          {src.file_name || "Source"}
                        </p>
                        <p className="text-xs text-gray-600 flex flex-wrap gap-2 mt-1">
                          {src.page_numbers && src.page_numbers.length > 0 && (
                            <span>Page(s): {src.page_numbers.join(", ")}</span>
                          )}
                          {src.clause_tags && src.clause_tags.length > 0 && (
                            <span>Tags: {src.clause_tags.join(", ")}</span>
                          )}
                          {src.section_heading && (
                            <span>Section: {src.section_heading}</span>
                          )}
                        </p>
                      </div>
                      <div className="flex items-center gap-3">
                        {src.clause_number && (
                          <span className="text-xs text-gray-500">
                            Clause {src.clause_number}
                          </span>
                        )}
                        {src.document_id && (
                          <button
                            type="button"
                            onClick={() => navigate(`/contracts/viewer/${src.document_id}`)}
                            className="shrink-0 inline-flex items-center gap-1 text-xs font-medium text-blue-600 hover:text-blue-800 hover:underline"
                            title="Open this document in the Contract Viewer"
                          >
                            <Eye className="h-3.5 w-3.5" />
                            View
                          </button>
                        )}
                      </div>
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {groupedClauseResults.length === 0 && !searching ? (
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
            ) : (
              <div className="space-y-4">
                {groupedClauseResults.map((clause) => (
                  <ClauseResultCard
                    key={clause.id}
                    clause={clause}
                    renderHighlighted={renderHighlighted}
                    expandedQuery={expandedQuery}
                    onViewSource={(docId) => navigate(`/contracts/viewer/${docId}`)}
                  />
                ))}
              </div>
            )}

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
              <button
                className="px-3 py-2 border rounded-md text-sm flex items-center gap-1 disabled:opacity-50"
                onClick={() => {
                  if (!searchResult?.has_more) return;
                  const next = page + 1;
                  setPage(next);
                  doSearch(expandedQuery, next);
                }}
                disabled={!searchResult?.has_more || searching}
                title={searchResult?.has_more ? "Next page" : "No more items"}
              >
                Next
                <ChevronRight className="h-4 w-4" />
              </button>
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
                          {search.query} | {search.categories.length} categories
                          {search.document_id && " | Specific file"}
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
                          {entry.document_id && " | Specific file"}
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
        limited to your authorized org/project scope. Categories apply keyword biasing.
      </div>
    </div>
  );
};

export default ContractsSearchPage;
export { ContractsSearchPage };
