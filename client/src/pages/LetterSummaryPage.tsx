import React, { useCallback, useEffect, useMemo, useState } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  FileText,
  BookOpen,
  Tag,
  Copy,
  Calendar as CalendarIcon,
  ExternalLink,
  ArrowLeft,
} from "lucide-react";
import { format } from "date-fns";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";

interface LetterOut {
  id: string;
  letterNo?: string;
  date?: string;
  subject?: string;
  from_?: string;
  to?: string;
  reference?: string[];
  keywords?: string[];
  Key_words?: string[];
  contractual_clauses?: string[];
  [key: string]: any;
}

interface Comment {
  id: string;
  text: string;
  author?: string;
  authorName?: string;
  authorEmail?: string;
  authorId?: string;
  authorRoles?: string[];
  createdAt?: string;
}


const parseBulletString = (s?: string): string[] => {
  if (!s) return [];
  return s
    .split(/\r?\n/)
    .map((ln) => ln.trim().replace(/^[−–—\-\*\d\.\)\s]+/, ""))
    .filter(Boolean);
};

const prettifyDate = (iso?: string) => {
  if (!iso) return "—";
  try {
    return format(new Date(iso), "MMM dd, yyyy");
  } catch {
    return iso;
  }
};

const LetterSummaryPage: React.FC = () => {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [doc, setDoc] = useState<LetterOut | null>(null);
  const [notes, setNotes] = useState("");
  const [comments, setComments] = useState<Comment[]>([]);
  const [newComment, setNewComment] = useState("");
  const [savingComment, setSavingComment] = useState(false);

  const loadComments = useCallback(async () => {
    if (!id) return;
    try {
      const res = await authenticatedFetch(joinApiUrl(`/documents/${id}/comments`));
      if (!res.ok) return;
      const data = await res.json();
      const list = Array.isArray(data) ? data : data?.comments ?? [];
      setComments(list);
    } catch {}
  }, [id]);

  const saveComment = async () => {
    if (!newComment.trim() || !id) return;
    setSavingComment(true);
    try {
      const res = await authenticatedFetch(joinApiUrl(`/documents/${id}/comments`), {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: newComment.trim() }),
      });
      if (!res.ok) return;
      setNewComment("");
      await loadComments();
    } catch {
    } finally {
      setSavingComment(false);
    }
  };

  useEffect(() => {
    loadComments();
  }, [loadComments]);

  useEffect(() => {
    const run = async () => {
      if (!id) return;
      setLoading(true);
      setError(null);
      try {
        const res = await authenticatedFetch(joinApiUrl(`/documents/${id}`));
        if (!res.ok) throw new Error(`Failed to fetch letter: ${res.status}`);
        const d = await res.json();
        const merged: LetterOut = {
          id: String(id),
          letterNo: d.letterNo ?? d.letter_no ?? undefined,
          date: d.date,
          subject: d.subject,
          // Accept either 'from' (API alias) or 'from_' (legacy/local)
          from_: d.from_ ?? d.from ?? undefined,
          to: d.to,
          reference: d.reference,
          keywords: d.keywords ?? d.Key_words ?? [],
          contractual_clauses:
            d.contractual_clauses ??
            d["Contractual Clauses"] ??
            d.clauses ??
            [],
          ...d,
        };
        setDoc(merged);
      } catch (e: any) {
        setError(e?.message || "Failed to fetch");
      } finally {
        setLoading(false);
      }
    };
    run();
  }, [id]);

  const keyPoints = useMemo(
    () => parseBulletString(doc?.summary),
    [doc?.summary]
  );

  const referenceLines = useMemo(() => {
    const r = (doc as any)?.reference;
    if (!r) return [];
    if (Array.isArray(r)) {
      return r
        .filter((x: any) => typeof x === "string" && x.trim())
        .map((s: string) => s.trim());
    }
    if (typeof r === "string") {
      return parseBulletString(r);
    }
    return [];
  }, [doc]);

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text);
    } catch {}
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-full p-12">
        <FileText className="animate-pulse h-6 w-6 mr-2" /> Loading...
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex items-center justify-center h-full p-12 text-red-600">
        {error}
      </div>
    );
  }

  if (!doc) {
    return (
      <div className="flex items-center justify-center h-full p-12">
        Not found
      </div>
    );
  }

  return (
    <div className="container mx-auto p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3">
          <Button variant="outline" size="sm" onClick={() => navigate(-1)}>
            <ArrowLeft className="h-4 w-4 mr-2" /> Back
          </Button>
          <div>
            <h1 className="text-2xl font-bold flex items-center gap-2">
              <BookOpen className="h-6 w-6" /> Letter Summary
            </h1>
            <p className="text-sm text-muted-foreground">
              Subject, key points, clauses, and references
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigate(`/reference/${doc.id}`)}
          >
            <ExternalLink className="h-4 w-4 mr-2" /> References
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() => navigate(`/documentviewer/${doc.id}`)}
          >
            <ExternalLink className="h-4 w-4 mr-2" /> Open Document
          </Button>
        </div>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <FileText className="h-4 w-4" /> {doc.subject || "—"}
          </CardTitle>
          <CardDescription>
            {doc.letterNo || "—"} • {prettifyDate(doc.date)}
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <div className="text-xs uppercase text-muted-foreground mb-1">
                From
              </div>
              <div className="font-medium">{doc.from_ || doc.from || "—"}</div>
            </div>
            <div>
              <div className="text-xs uppercase text-muted-foreground mb-1">
                To
              </div>
              <div className="font-medium">{doc.to || "—"}</div>
            </div>
          </div>
        </CardContent>
      </Card>

      <Tabs defaultValue="keypoints" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="keypoints">Summary</TabsTrigger>
          <TabsTrigger value="clauses">Contractual Clauses</TabsTrigger>
          <TabsTrigger value="reference">References</TabsTrigger>
        </TabsList>

        <TabsContent value="keypoints">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>Summary</CardTitle>
                <CardDescription>
                  Concise items captured from the letter
                </CardDescription>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  copyText(keyPoints.map((s) => `• ${s}`).join("\n"))
                }
              >
                <Copy className="h-4 w-4 mr-2" /> Copy
              </Button>
            </CardHeader>
            <CardContent>
              {keyPoints.length ? (
                <ul className="list-disc pl-5 space-y-1">
                  {keyPoints.map((s, i) => (
                    <li key={i} className="text-sm">
                      {s}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-sm text-muted-foreground">
                  No key points found.
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="clauses">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>Contractual Clauses</CardTitle>
                <CardDescription>Clauses mentioned or inferred</CardDescription>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() =>
                  copyText((doc.contractual_clauses || []).join("\n"))
                }
              >
                <Copy className="h-4 w-4 mr-2" /> Copy
              </Button>
            </CardHeader>
            <CardContent>
              {doc.contractual_clauses && doc.contractual_clauses.length ? (
                <ul className="list-disc pl-5 space-y-1">
                  {doc.contractual_clauses.map((c: string, i: number) => (
                    <li key={i} className="text-sm">
                      {c}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-sm text-muted-foreground">
                  No clauses found.
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="reference">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>References</CardTitle>
                <CardDescription>Auto-extracted references</CardDescription>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() => copyText(referenceLines.join("\n"))}
              >
                <Copy className="h-4 w-4 mr-2" /> Copy
              </Button>
            </CardHeader>
            <CardContent>
              {referenceLines.length ? (
                <ul className="list-disc pl-5 space-y-1">
                  {referenceLines.map((ref: string, i: number) => (
                    <li key={i} className="text-sm break-words">
                      {ref}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-sm text-muted-foreground">
                  No reference available.
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Card>
        <CardHeader>
          <CardTitle className="text-lg flex items-center gap-2">
            <Tag className="h-4 w-4" /> Keywords
          </CardTitle>
          <CardDescription>Detected key terms</CardDescription>
        </CardHeader>
        <CardContent>
          {doc.keywords && doc.keywords.length ? (
            <div className="flex flex-wrap gap-2">
              {doc.keywords.map((k: string, i: number) => (
                <Badge key={i} variant="neutral" className="text-sm py-1 px-2">
                  {k}
                </Badge>
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">
              No keywords detected.
            </div>
          )}
        </CardContent>
      </Card>

      <Separator />

      <Card>
        <CardHeader>
          <CardTitle>Comments</CardTitle>
          <CardDescription>Discussion</CardDescription>
        </CardHeader>
        <CardContent>
          {comments.length ? (
            <div className="space-y-3">
              {comments.map((c) => (
                <div key={c.id} className="rounded-md border p-3">
                  <div className="text-sm">{c.text}</div>
                  <div className="text-xs text-muted-foreground mt-1">
                    {c.author ? `${c.author} • ` : ""}
                    {c.createdAt ? prettifyDate(c.createdAt) : ""}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">
              No comments yet.
            </div>
          )}
          <div className="mt-4 space-y-2">
            <Textarea
              placeholder="Add a comment..."
              rows={3}
              value={newComment}
              onChange={(e) => setNewComment(e.target.value)}
            />
            <div className="flex justify-end">
              <Button
                onClick={saveComment}
                disabled={savingComment || !newComment.trim()}
              >
                {savingComment ? "Saving..." : "Add Comment"}
              </Button>
            </div>
          </div>
        </CardContent>
      </Card>

      <Separator />

      <Card>
        <CardHeader>
          <CardTitle>Notes</CardTitle>
          <CardDescription>Personal notes (local only)</CardDescription>
        </CardHeader>
        <CardContent>
          <Input
            placeholder="Add a short note title (optional)"
            className="mb-2"
          />
          <Textarea
            placeholder="Add notes..."
            rows={4}
            value={notes}
            onChange={(e) => setNotes(e.target.value)}
          />
          <div className="mt-3 flex justify-end">
            <Button variant="outline" onClick={() => copyText(notes)}>
              Copy Note
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
};

export default LetterSummaryPage;
