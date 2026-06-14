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

  const candidateFormats = ["dd-MM-yyyy", "dd/MM/yyyy", "yyyy-MM-dd"] as const;

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

const buildAuthHeaders = (): Record<string, string> => {
  return {};
};

const normalizeParsedReferences = (refData: any): RefParsed[] => {
  const refs: RefParsed[] = [];
  if (Array.isArray(refData)) {
    for (const ref of refData) {
      if (typeof ref === "string") {
        const raw = ref.trim();
        if (raw) refs.push({ raw });
      } else if (typeof ref === "object" && ref !== null) {
        const letterNoValue =
          ref.letter_no ?? ref.letterNo ?? ref.reference ?? ref.text ?? "";
        const dateValue = ref.date ?? ref.date_value ?? "";
        const rawText = ref.raw ?? ref.reference ?? ref.text ?? "";
        refs.push({
          raw: rawText || letterNoValue || JSON.stringify(ref),
          letterNo: letterNoValue || undefined,
          date: dateValue || undefined,
        });
      }
    }
  } else if (typeof refData === "string") {
    parseBulletString(refData).forEach((raw) => refs.push({ raw }));
  } else if (typeof refData === "object" && refData !== null) {
    const r = refData;
    refs.push({
      raw: r.raw ?? r.reference ?? r.text ?? "",
      letterNo: r.letter_no ?? r.letterNo ?? r.reference ?? r.text ?? undefined,
      date: r.date ?? r.date_value ?? undefined,
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
  const [syncing, setSyncing] = useState(false);

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
        if (!res.ok) throw new Error(`Failed to fetch letter: ${res.status}`);
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
      } catch (e: any) {
        setError(e.message);
        console.error(e);
      } finally {
        setLoading(false);
      }
    })();
  }, [letterId]);

  const fetchAndSetReferences = async (docId: string) => {
    try {
      const headers = buildAuthHeaders();
      const res = await authenticatedFetch(joinApiUrl(`/documents/${docId}/references`), {
        headers,
      });
      if (!res.ok) return;
      const data = await res.json();
      let parsed: RefParsed[] = [];
      let linked: RefLinked[] = [];

      if (data && ("parsed" in data || "linked" in data)) {
        parsed = (data.parsed || []).map((x: any) => {
          if (typeof x === "string") {
            return { raw: x } as RefParsed;
          }
          if (x && typeof x === "object") {
            const raw =
              x.letter_no ?? x.letterNo ?? x.raw ?? x.documentId ?? x.id ?? "";
            return {
              raw: String(raw),
              letterNo: x.letter_no ?? x.letterNo,
              date: x.date,
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

      setLetter((prev) => {
        if (!prev) return prev;
        const existingParsed = prev.referencesParsed || [];
        const merged = [...existingParsed, ...parsed];
        const seen = new Map<string, RefParsed>();
        for (const r of merged) {
          const key = String(r.raw ?? "")
            .toLowerCase()
            .trim();
          if (!key) continue;
          if (!seen.has(key)) {
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
    }
  };

  useEffect(() => {
    if (!letterId) return;
    fetchAndSetReferences(letterId);
  }, [letterId]);

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
      linked
        .map((l) => (l.letterNo ? String(l.letterNo).trim().toLowerCase() : ""))
        .filter(Boolean)
    );
    return parsed.filter((p) => {
      const ln = p.letterNo ? String(p.letterNo).trim().toLowerCase() : "";
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
        const detail = await res.text();
        throw new Error(detail || `Sync failed (${res.status})`);
      }
      toast.success("Reference sync completed");
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
      toast.error((err as Error)?.message || "Reference sync failed");
    } finally {
      setSyncing(false);
    }
  }, [letterId]);

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
      setError(e.message);
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
      <div className="flex items-center justify-center h-full text-red-500">
        Error: {error}
      </div>
    );
  }

  if (!letter) {
    return (
      <div className="flex items-center justify-center h-full">
        No letter found.
      </div>
    );
  }

  return (
    <div className="p-4 space-y-4">
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
