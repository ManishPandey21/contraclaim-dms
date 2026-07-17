import React, { useEffect, useMemo, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { Separator } from "@/components/ui/separator";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import {
  Calendar as CalendarIcon,
  FileText,
  LinkIcon,
  Loader2,
  ExternalLink,
  Search,
  Copy,
  Link2,
  ChevronRight,
  Info,
  Tag,
  BookOpen,
  RefreshCw,
  AlertCircle,
  ArrowLeft,
} from "lucide-react";
import { toast } from "sonner";
import { format, parse, parseISO, isValid } from "date-fns";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import { LANGGRAPH_ENABLED } from "@/config/features";
import GraphStatusBadge from "@/components/langgraph/GraphStatusBadge";

interface RefParsed {
  raw: string;
  date?: string;
  letterNo?: string;
  linkedId?: string | null;
}
interface RefLinked {
  id: string;
  title?: string;
  letterNo?: string;
  date?: string;
}

interface Letter {
  id: string;
  letterNo?: string;
  date?: string;
  subject?: string;
  from_?: string;
  to?: string;
  summary?: string;
  keywords?: string[];
  clauses?: string[];
  referencesParsed?: RefParsed[];
  referencesLinked?: RefLinked[];
  thread?: {
    threadId: string;
    ancestors: RefLinked[];
    descendants: RefLinked[];
    lastInThread: boolean;
  };
  graphStatus?: string;
  graphRunId?: string;
  draftPlan?: string;
}

const parseBulletString = (s?: string): string[] => {
  if (!s) return [];
  return s
    .split(/\r?\n/)
    .map((ln) => ln.trim().replace(/^[-*\d\.\)\s]+/, ""))
    .filter(Boolean);
};

const prettifyDate = (value?: string) => {
  if (!value) return "--";

  const trimmed = value.trim();
  if (!trimmed) return "--";

  const candidateFormats = [
    "dd-MM-yyyy",
    "dd/MM/yyyy",
    "dd.MM.yyyy",
    "yyyy-MM-dd",
  ] as const;

  const tryParse = (fmt: string) => {
    try {
      const parsed = parse(trimmed, fmt, new Date());
      return isValid(parsed) ? parsed : null;
    } catch {
      return null;
    }
  };

  let parsedDate: Date | null = null;

  for (const fmt of candidateFormats) {
    parsedDate = tryParse(fmt);
    if (parsedDate) break;
  }

  if (!parsedDate) {
    try {
      const isoParsed = parseISO(trimmed);
      parsedDate = isValid(isoParsed) ? isoParsed : null;
    } catch {
      parsedDate = null;
    }
  }

  if (!parsedDate) {
    try {
      const direct = new Date(trimmed);
      parsedDate = isValid(direct) ? direct : null;
    } catch {
      parsedDate = null;
    }
  }

  return parsedDate ? format(parsedDate, "dd-MM-yyyy") : trimmed;
};

const firstText = (...values: unknown[]) => {
  for (const value of values) {
    if (value === undefined || value === null) continue;
    const text = String(value).trim();
    if (text) return text;
  }
  return "";
};

const cleanReferenceLetterNo = (value: string) => {
  const labelPattern =
    /\b(?:LOA|letter|ltr|reference|ref)\s*(?:no|number|#)\.?\s*[:\-]?/gi;
  let candidate = value.trim();
  let match: RegExpExecArray | null;
  let lastLabelEnd = -1;

  while ((match = labelPattern.exec(candidate)) !== null) {
    lastLabelEnd = match.index + match[0].length;
  }

  if (lastLabelEnd >= 0) {
    candidate = candidate.slice(lastLabelEnd);
  }

  return candidate
    .replace(/\s+/g, " ")
    .trim()
    .replace(/^[\s:;,.()[\]#\-\u2013\u2014]+/, "")
    .replace(/[\s:;,.#\-\u2013\u2014]+$/, "")
    .trim();
};

const parseLegacyReferenceText = (value?: string): RefParsed | null => {
  const raw = firstText(value);
  if (!raw) return null;

  const match = raw.match(
    /^(.*?)\s*(?:[-\u2013\u2014]?\s*\b(?:dated|dtd|dt)\.?(?!\w))\s*[:\-]?\s*(\d{1,2}[.\/-]\d{1,2}[.\/-]\d{2,4})/i
  );
  if (!match) return null;

  const letterNo = cleanReferenceLetterNo(match[1] ?? "");
  if (!letterNo) return null;

  return {
    raw,
    letterNo,
    date: prettifyDate(match[2]),
  };
};

// Mirror of the backend's normalize_letter_code (falkor_graph_service.py):
// lowercase, collapse every non-alphanumeric run to "-", trim leading/trailing
// "-". Letter numbers often differ only in separators ("AFC/PM-06" vs
// "AFC-PM/06"), so missing/linked comparisons must use this canonical form.
const normalizeLetterCode = (value?: string) => {
  if (!value) return "";
  return value
    .trim()
    .toLowerCase()
    .replace(/[^0-9a-z]+/g, "-")
    .replace(/^-+|-+$/g, "");
};

const parsedReferenceKey = (ref: RefParsed) => {
  const raw = firstText(ref.raw);
  if (raw) return `raw:${raw.toLowerCase()}`;
  return [
    firstText(ref.letterNo).toLowerCase(),
    firstText(ref.date).toLowerCase(),
  ].join("|");
};

const parsedReferenceScore = (ref: RefParsed) => {
  const raw = firstText(ref.raw);
  const letterNo = firstText(ref.letterNo);
  const hasCleanLetter = letterNo && letterNo.toLowerCase() !== raw.toLowerCase();
  return (firstText(ref.date) ? 2 : 0) + (hasCleanLetter ? 3 : 0) + (raw ? 1 : 0);
};

const buildAuthHeaders = (): Record<string, string> => {
  return {};
};

const readApiError = async (response: Response, fallback: string) => {
  try {
    const body = await response.text();
    if (!body) return fallback;
    try {
      const parsed = JSON.parse(body);
      const detail = parsed?.detail ?? parsed?.message;
      if (typeof detail === "string" && detail.trim()) return detail.trim();
      return fallback;
    } catch {
      // Plain-text API errors are handled below.
    }
    return body.trim() || fallback;
  } catch {
    return fallback;
  }
};

const normalizeParsedReferences = (refData: any): RefParsed[] => {
  const refs: RefParsed[] = [];
  if (Array.isArray(refData)) {
    for (const ref of refData) {
      if (typeof ref === "string") {
        const raw = ref.trim();
        if (raw) refs.push(parseLegacyReferenceText(raw) ?? { raw });
      } else if (typeof ref === "object" && ref !== null) {
        const explicitLetter = firstText(
          ref.letter_no,
          ref.letterNo,
          ref.reference_number,
          ref.referenceNumber
        );
        const dateValue = firstText(ref.date, ref.date_value);
        const rawText = firstText(ref.raw, ref.text, ref.reference, explicitLetter);
        const parsed = parseLegacyReferenceText(rawText || explicitLetter);
        const letterNoValue = explicitLetter || parsed?.letterNo || "";
        const normalizedDate = dateValue || parsed?.date || "";
        refs.push({
          raw: rawText || parsed?.raw || letterNoValue || JSON.stringify(ref),
          letterNo: letterNoValue || undefined,
          date: normalizedDate || undefined,
        });
      }
    }
  } else if (typeof refData === "string") {
    parseBulletString(refData).forEach((raw) =>
      refs.push(parseLegacyReferenceText(raw) ?? { raw })
    );
  } else if (typeof refData === "object" && refData !== null) {
    const r = refData;
    const explicitLetter = firstText(
      r.letter_no,
      r.letterNo,
      r.reference_number,
      r.referenceNumber
    );
    const dateValue = firstText(r.date, r.date_value);
    const rawText = firstText(r.raw, r.text, r.reference, explicitLetter);
    const parsed = parseLegacyReferenceText(rawText || explicitLetter);
    refs.push({
      raw: rawText || parsed?.raw || explicitLetter,
      letterNo: explicitLetter || parsed?.letterNo || undefined,
      date: dateValue || parsed?.date || undefined,
    });
  }
  return refs;
};

const ReferencePage: React.FC = () => {
  const params = useParams<{ id?: string }>();
  const letterId = params.id;
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [letter, setLetter] = useState<Letter | null>(null);
  const [query, setQuery] = useState("");
  const [linkDialogOpen, setLinkDialogOpen] = useState(false);
  const [linking, setLinking] = useState(false);
  const [newRef, setNewRef] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [syncing, setSyncing] = useState(false);

  const fetchAndSetReferences = useCallback(async (docId: string) => {
    try {
      const headers = buildAuthHeaders();
      const res = await authenticatedFetch(joinApiUrl(`/documents/${docId}/references`), {
        headers,
      });
      if (!res.ok) {
        throw new Error(
          await readApiError(
            res,
            "We couldn't load the linked references for this letter."
          )
        );
      }
      const data = await res.json();
      let parsed: RefParsed[] = [];
      let linked: RefLinked[] = [];

      if (data && ("parsed" in data || "linked" in data)) {
        parsed = (data.parsed || []).map((x: any) => {
          if (typeof x === "string") {
            return { raw: x } as RefParsed;
          }
          if (x && typeof x === "object") {
            const raw = firstText(
              x.raw,
              x.reference,
              x.text,
              x.letter_no,
              x.letterNo,
              x.documentId,
              x.id
            );
            const parsedLegacy = parseLegacyReferenceText(raw);
            return {
              raw,
              letterNo: firstText(x.letter_no, x.letterNo, parsedLegacy?.letterNo) || undefined,
              date: firstText(x.date, x.date_value, parsedLegacy?.date) || undefined,
              linkedId: x.documentId ?? x.id ?? null,
            } as RefParsed;
          }
          return { raw: String(x ?? "") } as RefParsed;
        });
        linked = (data.linked || []).map((x: any) => ({
          id: x.id ?? x.documentId,
          letterNo: x.letterNo != null ? String(x.letterNo) : undefined,
          title: x.title != null ? String(x.title) : undefined,
          date: x.date != null ? String(x.date) : undefined,
        }));
      } else if (Array.isArray(data)) {
        const refs: { documentId: string }[] = data;
        linked = await Promise.all(
          refs.map(async (r) => {
            try {
              const rd = await authenticatedFetch(joinApiUrl(`/documents/${r.documentId}`), {
                headers,
              });
              if (!rd.ok) throw new Error();
              const dj = await rd.json();
              return {
                id: r.documentId,
                letterNo: dj.letterNo,
                title: dj.subject,
                date: dj.date,
              } as RefLinked;
            } catch {
              return { id: r.documentId } as RefLinked;
            }
          })
        );
      }

      // One row per target document: legacy data can hold the same link under
      // both parser and manual sources, which also breaks React keys.
      const seenLinkedIds = new Set<string>();
      linked = linked.filter((ref) => {
        const key = String(ref.id ?? "");
        if (!key || seenLinkedIds.has(key)) return false;
        seenLinkedIds.add(key);
        return true;
      });

      setLetter((prev) => {
        if (!prev) return prev;
        const existingParsed = prev.referencesParsed || [];
        const merged = [...existingParsed, ...parsed];
        const seen = new Map<string, RefParsed>();
        for (const r of merged) {
          const key = parsedReferenceKey(r);
          if (!key) continue;
          const existing = seen.get(key);
          if (!existing || parsedReferenceScore(r) > parsedReferenceScore(existing)) {
            seen.set(key, r);
          }
        }
        return {
          ...prev,
          referencesParsed: Array.from(seen.values()),
          referencesLinked: linked,
        };
      });
    } catch (err) {
      console.error("Failed to fetch references", err);
      setActionError(
        err instanceof Error
          ? err.message
          : "We couldn't load the linked references for this letter."
      );
    }
  }, []);

  useEffect(() => {
    if (!letterId) return;
    setLoading(true);
    setError(null);
    (async () => {
      try {
        const headers = buildAuthHeaders();
        const res = await authenticatedFetch(joinApiUrl(`/documents/${letterId}`), {
          headers,
        });
        if (!res.ok) {
          const fallback =
            res.status === 404
              ? "This letter could not be found or you no longer have access to it."
              : res.status === 403
              ? "You do not have permission to view this letter."
              : "We couldn't load this letter. Please try again.";
          throw new Error(await readApiError(res, fallback));
        }
        const d = await res.json();
        // Normalize references extracted by metadata.py from DB (array or bullet string)
        // const refsFromDoc: string[] = Array.isArray(d.reference)
        //   ? (d.reference || [])
        //      .filter((x: any) => typeof x === "string")
        //     .map((s: string) => s.trim())
        //     .filter(Boolean)
        //  : typeof d.reference === "string"
        //  ? parseBulletString(d.reference)
        //  : [];

        const refsFromDoc: RefParsed[] = normalizeParsedReferences(
          d.reference
        ).map((r) => ({
          ...r,
          linkedId: (r as any).linkedId ?? null,
        }));

        const l: Letter = {
          id: String(letterId),
          letterNo: d.letterNo ?? d.letter_no ?? undefined,
          date: d.date,
          subject: d.subject,
          from_: d.from_ ?? d.from ?? undefined,
          to: d.to,
          summary: d.summary,
          keywords: d.keywords ?? d.Key_words ?? [],
          clauses: d.contractual_clauses ?? d.clauses ?? [],
          referencesParsed: refsFromDoc, //.map((raw) => ({ raw })),
          referencesLinked: [],
          graphStatus: d.graph_status ?? d.graphStatus ?? undefined,
          graphRunId: d.graph_run_id ?? d.graphRunId ?? undefined,
          draftPlan: d.draft_plan ?? d.draftPlan ?? undefined,
          thread: {
            threadId: d.chain_id || d.chain_head_id || "",
            ancestors: [],
            descendants: [],
            lastInThread: true,
          },
        };
        setLetter(l);
        // Load linked references only after the letter state exists: firing
        // both requests independently races, and whichever lands last used to
        // wipe or drop the linked list (empty Linked tab after a refresh).
        await fetchAndSetReferences(String(letterId));
      } catch (e: any) {
        setError(e.message);
        console.error(e);
      } finally {
        setLoading(false);
      }
    })();
  }, [letterId, fetchAndSetReferences]);

  const summaryLines = useMemo(
    () => parseBulletString(letter?.summary),
    [letter?.summary]
  );

  const filteredParsed = useMemo(() => {
    const base = letter?.referencesParsed || [];
    const q = query.trim().toLowerCase();
    if (!q) return base;
    return base.filter((r) => {
      const rawText = String(r.raw ?? "").toLowerCase();
      const letterText = (r.letterNo ?? "").toLowerCase();
      const dateText = (r.date ?? "").toLowerCase();
      return (
        rawText.includes(q) || letterText.includes(q) || dateText.includes(q)
      );
    });
  }, [query, letter?.referencesParsed]);

  const missingRefs = useMemo(() => {
    const parsed = letter?.referencesParsed || [];
    const linked = letter?.referencesLinked || [];
    const linkedNos = new Set(
      linked.map((l) => normalizeLetterCode(l.letterNo)).filter(Boolean)
    );
    return parsed.filter((p) => {
      const ln = normalizeLetterCode(p.letterNo);
      const hasLinked = ln && linkedNos.has(ln);
      return !hasLinked && !p.linkedId;
    });
  }, [letter?.referencesParsed, letter?.referencesLinked]);

  const filteredMissing = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return missingRefs;
    return missingRefs.filter((r) => {
      const rawText = String(r.raw ?? "").toLowerCase();
      const letterText = (r.letterNo ?? "").toLowerCase();
      const dateText = (r.date ?? "").toLowerCase();
      return (
        rawText.includes(q) || letterText.includes(q) || dateText.includes(q)
      );
    });
  }, [missingRefs, query]);

  const copySummary = async () => {
    if (!summaryLines.length) return;
    await navigator.clipboard.writeText(
      summaryLines.map((s) => `â€¢ ${s}`).join("\n")
    );
  };

  const copyClauses = async () => {
    if (!letter?.clauses?.length) return;
    await navigator.clipboard.writeText(letter.clauses.join("\n"));
  };

  const openLinked = (id?: string | null) => {
    if (!id) return;
    window.open(`/documentviewer/${id}?tab=references`, "_blank");
  };

  const openLinkDialog = useCallback((prefill?: string) => {
    setNewRef(prefill ?? "");
    setLinkDialogOpen(true);
  }, []);

  const handleDialogOpenChange = useCallback((open: boolean) => {
    setLinkDialogOpen(open);
    if (!open) {
      setNewRef("");
    }
  }, []);

  const runReferenceSync = useCallback(async () => {
    if (!letterId) return;
    setSyncing(true);
    setActionError(null);
    try {
      const headers = buildAuthHeaders();
      const res = await authenticatedFetch(
        joinApiUrl(`/documents/${letterId}/sync-references`),
        {
          method: "POST",
          headers,
        }
      );
      if (!res.ok) {
        throw new Error(
          await readApiError(
            res,
            "We couldn't sync this letter's references. Please try again."
          )
        );
      }
      const result = await res.json();
      toast.success(result?.message || "Reference sync completed");
      // Refresh data
      const r = await authenticatedFetch(joinApiUrl(`/documents/${letterId}`), { headers });
      if (r.ok) {
        const d = await r.json();
        const refsFromDoc = normalizeParsedReferences(d.reference);
        setLetter((prev) =>
          prev
            ? {
                ...prev,
                referencesParsed: refsFromDoc,
              }
            : prev
        );
      }
      await fetchAndSetReferences(letterId);
    } catch (err) {
      console.error(err);
      const message =
        err instanceof Error && err.message
          ? err.message
          : "We couldn't sync this letter's references. Please try again.";
      setActionError(message);
      toast.error("Reference sync could not be completed", {
        description: message,
      });
    } finally {
      setSyncing(false);
    }
  }, [fetchAndSetReferences, letterId]);

  const linkReference = async () => {
    if (!newRef.trim() || !letter) return;
    setLinking(true);
    try {
      // Lookup referenced document by letter number
      const searchRes = await authenticatedFetch(
        joinApiUrl(`/documents?letterNo=${encodeURIComponent(newRef.trim())}`),
        {}
      );
      if (!searchRes.ok) throw new Error(`Lookup failed: ${searchRes.status}`);
      const searchData = await searchRes.json();
      const target =
        Array.isArray(searchData?.documents) && searchData.documents.length
          ? searchData.documents[0]
          : Array.isArray(searchData) && searchData.length
          ? searchData[0]
          : null;
      const targetId = target?.id || target?._id;
      if (!targetId) throw new Error("Referenced document not found");

      const postHeaders: Record<string, string> = {
        "Content-Type": "application/json",
        ...buildAuthHeaders(),
      };
      const res = await authenticatedFetch(
        joinApiUrl(`/documents/${letter.id}/references`),
        {
          method: "POST",
          headers: postHeaders,
          body: JSON.stringify({
            referenced_document_id: String(targetId),
            link_type: "direct",
          }),
        }
      );
      if (!res.ok) throw new Error(`Failed to link reference: ${res.status}`);

      setNewRef("");
      handleDialogOpenChange(false);

      // Refresh linked references
      await fetchAndSetReferences(letter.id);
    } catch (e: any) {
      console.error(e);
      setActionError(
        e?.message || "We couldn't link this reference. Please try again."
      );
    } finally {
      setLinking(false);
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full">
        <Loader2 className="animate-spin h-8 w-8" />
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center p-4">
        <Card className="w-full max-w-lg border-destructive/40">
          <CardHeader>
            <CardTitle className="flex items-center gap-2">
              <AlertCircle className="h-5 w-5 text-destructive" />
              Unable to open references
            </CardTitle>
            <CardDescription>{error}</CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" onClick={() => navigate(-1)}>
              <ArrowLeft className="mr-2 h-4 w-4" />
              Return to previous page
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  if (!letter) {
    return (
      <div className="flex min-h-[60vh] items-center justify-center p-4">
        <Card className="w-full max-w-lg">
          <CardHeader>
            <CardTitle>Letter not found</CardTitle>
            <CardDescription>
              This letter is unavailable or may have been removed.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Button variant="outline" onClick={() => navigate(-1)}>
              <ArrowLeft className="mr-2 h-4 w-4" />
              Return to previous page
            </Button>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
      <h1 className="text-2xl font-bold">Document References</h1>
      {actionError && (
        <Card className="border-destructive/40 bg-destructive/5">
          <CardContent className="flex flex-col gap-3 pt-6 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex gap-3">
              <AlertCircle className="mt-0.5 h-5 w-5 shrink-0 text-destructive" />
              <div>
                <p className="font-medium">Reference action could not be completed</p>
                <p className="text-sm text-muted-foreground">{actionError}</p>
              </div>
            </div>
            <Button variant="outline" onClick={() => navigate(-1)}>
              <ArrowLeft className="mr-2 h-4 w-4" />
              Return to previous page
            </Button>
          </CardContent>
        </Card>
      )}
      <Card>
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle>{String(letter.subject ?? "")}</CardTitle>
            <CardDescription>
              {String(letter.letterNo ?? "")} - {prettifyDate(letter.date)}
            </CardDescription>
            {LANGGRAPH_ENABLED && (
              <div className="mt-2 flex items-center gap-2 text-sm text-muted-foreground">
                <span>LangGraph:</span>
                <GraphStatusBadge status={letter.graphStatus ?? null} />
              </div>
            )}
          </div>
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
            {LANGGRAPH_ENABLED && (
              <Button
                size="sm"
                onClick={() => navigate(`/letters/${letter.id}/draft`)}
              >
                Open Draft Workspace
              </Button>
            )}
            <Button
              variant="outline"
              size="sm"
              onClick={() => navigate(`/documents/summary/${letter.id}`)}
            >
              <BookOpen className="h-4 w-4 mr-2" />
              Summary
            </Button>
          </div>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-2 gap-4">
            <div>
              <h3 className="font-semibold">From</h3>
              <p>{String(letter.from_ ?? "")}</p>
            </div>
            <div>
              <h3 className="font-semibold">To</h3>
              <p>{String(letter.to ?? "")}</p>
            </div>
          </div>
          <div className="mt-4 flex flex-wrap items-center justify-between gap-2">
            <Badge variant="outline">
              <CalendarIcon className="h-3 w-3 mr-1" />
              {letter?.date || "Unknown date"}
            </Badge>
            <Button
              variant="secondary"
              size="sm"
              onClick={runReferenceSync}
              disabled={syncing}
              className="flex items-center gap-2"
            >
              {syncing ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              Sync References
            </Button>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="linked">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="linked">Linked References</TabsTrigger>
          <TabsTrigger value="parsed">Parsed References</TabsTrigger>
          <TabsTrigger value="missing">
            Missing References ({missingRefs.length})
          </TabsTrigger>
        </TabsList>
        <TabsContent value="linked">
          <Card>
            <CardHeader>
              <CardTitle>Linked References</CardTitle>
              <CardDescription>
                References that have been successfully linked to other
                documents.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Letter No.</TableHead>
                    <TableHead>Title</TableHead>
                    <TableHead>Date</TableHead>
                    <TableHead></TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {letter.referencesLinked?.map((ref) => (
                    <TableRow key={ref.id}>
                      <TableCell>{ref.letterNo}</TableCell>
                      <TableCell>{ref.title}</TableCell>
                      <TableCell>{prettifyDate(ref.date)}</TableCell>
                      <TableCell>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => openLinked(ref.id)}
                        >
                          <ExternalLink className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="parsed">
          <Card>
            <CardHeader>
              <CardTitle>Parsed References</CardTitle>
              <CardDescription>
                References that were parsed from the document but not yet
                linked.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center space-x-2 mb-4">
                <Input
                  placeholder="Search parsed references..."
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
                <Button onClick={() => openLinkDialog("")}>
                  Link New Reference
                </Button>
                <Dialog
                  open={linkDialogOpen}
                  onOpenChange={handleDialogOpenChange}
                >
                  <DialogContent>
                    <DialogHeader>
                      <DialogTitle>Link New Reference</DialogTitle>
                      <DialogDescription>
                        Enter the letter number of the document you want to
                        link.
                      </DialogDescription>
                    </DialogHeader>
                    <div className="space-y-4">
                      <Input
                        placeholder="Letter No."
                        value={newRef}
                        onChange={(e) => setNewRef(e.target.value)}
                      />
                    </div>
                    <DialogFooter>
                      <Button
                        variant="outline"
                        onClick={() => handleDialogOpenChange(false)}
                      >
                        Cancel
                      </Button>
                      <Button onClick={linkReference} disabled={linking}>
                        {linking && (
                          <Loader2 className="animate-spin h-4 w-4 mr-2" />
                        )}
                        Link
                      </Button>
                    </DialogFooter>
                  </DialogContent>
                </Dialog>
              </div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Letter No.</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredParsed.map((ref, i) => {
                    const rawValue = String(ref.raw ?? "");
                    const displayDate = ref.date
                      ? prettifyDate(ref.date)
                      : "--";
                    const displayLetter = ref.letterNo ?? (rawValue || "--");
                    const prefillValue = ref.letterNo ?? "";
                    return (
                      <TableRow key={i}>
                        <TableCell>{displayDate}</TableCell>
                        <TableCell>{displayLetter}</TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => openLinkDialog(prefillValue.trim())}
                          >
                            <Link2 className="h-4 w-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>
        <TabsContent value="missing">
          <Card>
            <CardHeader>
              <CardTitle>Missing Documents</CardTitle>
              <CardDescription>
                Parsed references that are not linked to any document yet.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="flex items-center space-x-2 mb-4">
                <Input
                  placeholder="Search missing references..."
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Date</TableHead>
                    <TableHead>Letter No.</TableHead>
                    <TableHead className="text-right">Actions</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredMissing.map((ref, i) => {
                    const rawValue = String(ref.raw ?? "");
                    const displayDate = ref.date
                      ? prettifyDate(ref.date)
                      : "--";
                    const displayLetter = ref.letterNo ?? (rawValue || "--");
                    const prefillValue = ref.letterNo ?? rawValue ?? "";
                    return (
                      <TableRow key={i}>
                        <TableCell>{displayDate}</TableCell>
                        <TableCell>{displayLetter}</TableCell>
                        <TableCell className="text-right">
                          <Button
                            variant="outline"
                            size="sm"
                            onClick={() => openLinkDialog(prefillValue.trim())}
                          >
                            <Link2 className="h-4 w-4" />
                          </Button>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                  {filteredMissing.length === 0 && (
                    <TableRow>
                      <TableCell
                        colSpan={3}
                        className="text-center text-sm text-muted-foreground"
                      >
                        All parsed references are linked.
                      </TableCell>
                    </TableRow>
                  )}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>
    </div>
  );
};

export default ReferencePage;
