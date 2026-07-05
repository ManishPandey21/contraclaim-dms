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
import { Badge, type BadgeProps } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  FileText,
  BookOpen,
  Tag,
  Copy,
  ExternalLink,
  ArrowLeft,
  MapPin,
  Pencil,
  Plus,
  X,
} from "lucide-react";
import { format } from "date-fns";
import { toast } from "sonner";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import useHasPermission from "@/hooks/useHasPermission";

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
  key_reply_points?: string[];
  additional_keywords?: string[];
  extracted_tags?: string[];
  extracted_subTags?: string[];
  asset_type?: string;
  location?: string;
  specific_area?: string;
  chainage_from?: string;
  chainage_to?: string;
  work_type?: string;
  issue_nature?: string;
  claim_category?: string;
  alleged_responsibility?: string;
  priority?: string;
  linked_event_suggested?: string;
  reference_chain?: string;
  metadata?: Record<string, any>;
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

type MetadataFormState = {
  asset_type: string;
  location: string;
  work_type: string;
  issue_nature: string;
  claim_category: string;
  responsibility: string;
};

type KeywordFormState = {
  keywords: string[];
  additional_keywords: string[];
  extracted_tags: string[];
  extracted_subTags: string[];
};

const EMPTY_METADATA_FORM: MetadataFormState = {
  asset_type: "",
  location: "",
  work_type: "",
  issue_nature: "",
  claim_category: "",
  responsibility: "",
};

const EMPTY_KEYWORD_FORM: KeywordFormState = {
  keywords: [],
  additional_keywords: [],
  extracted_tags: [],
  extracted_subTags: [],
};


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

const NULLISH_VALUES = new Set([
  "",
  "null",
  "'null'",
  '"null"',
  "not found",
  "none",
  "n/a",
  "na",
  "not applicable",
  "-",
  "--",
]);

const cleanScalar = (value: any): string | undefined => {
  if (value === null || value === undefined) return undefined;
  const text = String(value).trim();
  if (!text || NULLISH_VALUES.has(text.toLowerCase())) return undefined;
  return text;
};

const normalizeStringList = (value: any): string[] => {
  const rawItems = Array.isArray(value) ? value : value ? [value] : [];
  const items = rawItems.flatMap((item) => {
    if (item === null || item === undefined) return [];
    if (typeof item === "object") {
      const text =
        cleanScalar(item.text) ||
        cleanScalar(item.raw) ||
        cleanScalar(item.letterNo) ||
        cleanScalar(item.letter_no);
      return text ? [text] : [];
    }
    return String(item)
      .split(/\r?\n|,/)
      .map((part) => part.trim().replace(/^[\-\*\d\.\)\s]+/, ""));
  });

  const seen = new Set<string>();
  return items
    .map(cleanScalar)
    .filter((item): item is string => Boolean(item))
    .filter((item) => {
      const key = item.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
};

const normalizeEditableList = (value: any): string[] => {
  const rawItems = Array.isArray(value) ? value : value ? [value] : [];
  const seen = new Set<string>();
  const normalized: string[] = [];
  rawItems.forEach((item) => {
    String(item ?? "")
      .split(/\r?\n|,/)
      .map((part) => part.trim().replace(/\s+/g, " "))
      .filter(Boolean)
      .forEach((part) => {
        const key = part.toLowerCase();
        if (seen.has(key)) return;
        seen.add(key);
        normalized.push(part);
      });
  });
  return normalized;
};

const firstScalar = (source: any, ...keys: string[]): string | undefined => {
  for (const key of keys) {
    const value = cleanScalar(source?.[key]);
    if (value) return value;
  }
  const metadata = source?.metadata;
  for (const key of keys) {
    const value = cleanScalar(metadata?.[key]);
    if (value) return value;
  }
  return undefined;
};

const firstList = (source: any, ...keys: string[]): string[] => {
  for (const key of keys) {
    const values = normalizeStringList(source?.[key]);
    if (values.length) return values;
  }
  const metadata = source?.metadata;
  for (const key of keys) {
    const values = normalizeStringList(metadata?.[key]);
    if (values.length) return values;
  }
  return [];
};

const priorityVariant = (priority?: string) => {
  const normalized = (priority || "").toLowerCase();
  if (normalized === "critical") return "danger";
  if (normalized === "high") return "warning";
  if (normalized === "low") return "outline";
  return "neutral";
};

const normalizeLetter = (d: any, fallbackId: string): LetterOut => {
  const docId = d.id ?? d._id ?? fallbackId;
  return {
    ...d,
    id: String(docId),
    letterNo: firstScalar(d, "letterNo", "letter_no"),
    date: d.date,
    subject: firstScalar(d, "subject"),
    from_: firstScalar(d, "from_", "from", "from_company"),
    to: firstScalar(d, "to", "to_company"),
    reference: d.reference,
    keywords: firstList(d, "keywords", "Key_words"),
    additional_keywords: firstList(
      d,
      "additional_keywords",
      "additionalKeywords"
    ),
    contractual_clauses:
      firstList(d, "contractual_clauses", "Contractual Clauses", "clauses"),
    key_reply_points: firstList(d, "key_reply_points", "keyReplyPoints"),
    extracted_tags:
      normalizeStringList(d.extracted_tags).length
        ? normalizeStringList(d.extracted_tags)
        : normalizeStringList(d.metadata?.tags),
    extracted_subTags:
      normalizeStringList(d.extracted_subTags).length
        ? normalizeStringList(d.extracted_subTags)
        : normalizeStringList(d.metadata?.subTags ?? d.metadata?.sub_tags),
    asset_type: firstScalar(d, "asset_type", "assetType"),
    location: firstScalar(d, "location"),
    specific_area: firstScalar(d, "specific_area", "specificArea"),
    chainage_from: firstScalar(d, "chainage_from", "chainageFrom"),
    chainage_to: firstScalar(d, "chainage_to", "chainageTo"),
    work_type: firstScalar(d, "work_type", "workType"),
    issue_nature: firstScalar(d, "issue_nature", "issueNature"),
    claim_category: firstScalar(d, "claim_category", "claimCategory"),
    alleged_responsibility: firstScalar(
      d,
      "alleged_responsibility",
      "allegedResponsibility",
      "responsibility"
    ),
    priority: firstScalar(d, "priority"),
    linked_event_suggested: firstScalar(
      d,
      "linked_event_suggested",
      "linkedEventSuggested"
    ),
    reference_chain: firstScalar(d, "reference_chain", "referenceChain"),
  };
};

const metadataFormFromDoc = (doc: LetterOut | null): MetadataFormState => ({
  asset_type: doc?.asset_type || "",
  location: doc?.location || "",
  work_type: doc?.work_type || "",
  issue_nature: doc?.issue_nature || "",
  claim_category: doc?.claim_category || "",
  responsibility: doc?.alleged_responsibility || "",
});

const keywordFormFromDoc = (doc: LetterOut | null): KeywordFormState => ({
  keywords: normalizeEditableList(doc?.keywords),
  additional_keywords: normalizeEditableList(doc?.additional_keywords),
  extracted_tags: normalizeEditableList(doc?.extracted_tags),
  extracted_subTags: normalizeEditableList(doc?.extracted_subTags),
});

const ChipListEditor: React.FC<{
  label: string;
  items: string[];
  placeholder: string;
  onChange: (items: string[]) => void;
}> = ({ label, items, placeholder, onChange }) => {
  const [draft, setDraft] = useState("");

  const addDraft = () => {
    const additions = normalizeEditableList(draft);
    if (!additions.length) return;
    onChange(normalizeEditableList([...items, ...additions]));
    setDraft("");
  };

  const updateItem = (index: number, value: string) => {
    onChange(items.map((item, itemIndex) => (itemIndex === index ? value : item)));
  };

  const removeItem = (index: number) => {
    onChange(items.filter((_, itemIndex) => itemIndex !== index));
  };

  const normalizeCurrent = () => {
    onChange(normalizeEditableList(items));
  };

  return (
    <div className="space-y-2">
      <Label className="text-xs uppercase text-muted-foreground">{label}</Label>
      <div className="flex flex-wrap gap-2 rounded-md border bg-white p-2">
        {items.map((item, index) => (
          <span
            key={`${label}-${index}`}
            className="inline-flex max-w-full items-center gap-1 rounded-full border bg-gray-50 px-2 py-1"
          >
            <input
              className="h-6 min-w-[7rem] max-w-[14rem] bg-transparent text-sm outline-none"
              value={item}
              onBlur={normalizeCurrent}
              onChange={(event) => updateItem(index, event.target.value)}
              aria-label={`${label} item ${index + 1}`}
            />
            <button
              type="button"
              className="rounded-full p-0.5 text-gray-500 hover:bg-gray-200 hover:text-gray-900"
              onClick={() => removeItem(index)}
              aria-label={`Remove ${item || label}`}
            >
              <X className="h-3.5 w-3.5" />
            </button>
          </span>
        ))}
        <div className="flex min-w-[14rem] flex-1 items-center gap-2">
          <Input
            className="h-8"
            placeholder={placeholder}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addDraft();
              }
            }}
          />
          <Button type="button" variant="outline" size="sm" onClick={addDraft}>
            <Plus className="h-4 w-4" /> Add
          </Button>
        </div>
      </div>
    </div>
  );
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
  const canEditMetadata = useHasPermission("dms.document.edit_metadata");
  const [metadataDialogOpen, setMetadataDialogOpen] = useState(false);
  const [keywordDialogOpen, setKeywordDialogOpen] = useState(false);
  const [metadataForm, setMetadataForm] =
    useState<MetadataFormState>(EMPTY_METADATA_FORM);
  const [keywordForm, setKeywordForm] =
    useState<KeywordFormState>(EMPTY_KEYWORD_FORM);
  const [savingSummaryMetadata, setSavingSummaryMetadata] = useState(false);

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
        setDoc(normalizeLetter(d, id));
      } catch (e: any) {
        setError(e?.message || "Failed to fetch");
      } finally {
        setLoading(false);
      }
    };
    run();
  }, [id]);

  const openMetadataEditor = () => {
    setMetadataForm(metadataFormFromDoc(doc));
    setMetadataDialogOpen(true);
  };

  const openKeywordEditor = () => {
    setKeywordForm(keywordFormFromDoc(doc));
    setKeywordDialogOpen(true);
  };

  const readErrorMessage = async (res: Response) => {
    try {
      const body = await res.json();
      return body?.detail || body?.message || `Request failed: ${res.status}`;
    } catch {
      return `Request failed: ${res.status}`;
    }
  };

  const saveSummaryMetadata = async (payload: Record<string, any>) => {
    if (!id) return;
    setSavingSummaryMetadata(true);
    try {
      const res = await authenticatedFetch(
        joinApiUrl(`/documents/${id}/summary-metadata`),
        {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(payload),
        }
      );
      if (!res.ok) throw new Error(await readErrorMessage(res));
      const updated = await res.json();
      setDoc(normalizeLetter(updated, id));
      toast.success("Letter summary metadata saved");
      setMetadataDialogOpen(false);
      setKeywordDialogOpen(false);
    } catch (err: any) {
      toast.error(err?.message || "Failed to save letter summary metadata");
    } finally {
      setSavingSummaryMetadata(false);
    }
  };

  const saveMetadataForm = () => {
    saveSummaryMetadata({
      extracted_metadata: {
        asset_type: metadataForm.asset_type.trim(),
        location: metadataForm.location.trim(),
        work_type: metadataForm.work_type.trim(),
        issue_nature: metadataForm.issue_nature.trim(),
        claim_category: metadataForm.claim_category.trim(),
        responsibility: metadataForm.responsibility.trim(),
      },
    });
  };

  const saveKeywordForm = () => {
    saveSummaryMetadata({
      keywords: normalizeEditableList(keywordForm.keywords),
      additional_keywords: normalizeEditableList(keywordForm.additional_keywords),
      extracted_tags: normalizeEditableList(keywordForm.extracted_tags),
      extracted_sub_tags: normalizeEditableList(keywordForm.extracted_subTags),
    });
  };

  const keyPoints = useMemo(
    () => parseBulletString(doc?.summary),
    [doc?.summary]
  );

  const keyReplyPoints = useMemo(() => {
    const k = (doc as any)?.key_reply_points;
    if (!k) return [];
    if (Array.isArray(k)) {
      return k
        .filter((x: any) => typeof x === "string" && x.trim())
        .map((s: string) => s.trim());
    }
    if (typeof k === "string") {
      return parseBulletString(k);
    }
    return [];
  }, [doc]);

  const metadataFields = useMemo(() => {
    if (!doc) return [];
    const chainage =
      doc.chainage_from && doc.chainage_to
        ? `${doc.chainage_from} to ${doc.chainage_to}`
        : doc.chainage_from || doc.chainage_to;

    return [
      { label: "Asset Type", value: doc.asset_type },
      { label: "Location", value: doc.location },
      { label: "Specific Area", value: doc.specific_area },
      { label: "Chainage", value: chainage },
      { label: "Work Type", value: doc.work_type },
      { label: "Issue Nature", value: doc.issue_nature },
      { label: "Claim Category", value: doc.claim_category },
      { label: "Responsibility", value: doc.alleged_responsibility },
      { label: "Priority", value: doc.priority, badge: true },
      { label: "Reference Chain", value: doc.reference_chain },
      {
        label: "Linked Event Suggested",
        value: doc.linked_event_suggested,
        wide: true,
      },
    ].filter((field) => cleanScalar(field.value));
  }, [doc]);

  const keywordGroups = useMemo<
    Array<{
      label: string;
      items: string[];
      variant: BadgeProps["variant"];
    }>
  >(() => {
    if (!doc) return [];
    return [
      { label: "Keywords", items: doc.keywords || [], variant: "neutral" },
      {
        label: "Additional Keywords",
        items: doc.additional_keywords || [],
        variant: "outline",
      },
      {
        label: "Extracted Tags",
        items: doc.extracted_tags || [],
        variant: "primary",
      },
      {
        label: "Extracted Sub-tags",
        items: doc.extracted_subTags || [],
        variant: "outline",
      },
    ].filter((group) => group.items.length);
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

      <Card>
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle className="text-lg flex items-center gap-2">
              <MapPin className="h-4 w-4" /> Extracted Metadata
            </CardTitle>
            <CardDescription>
              Asset, location, claim, responsibility, and chronology fields
            </CardDescription>
          </div>
          {canEditMetadata && (
            <Button variant="outline" size="sm" onClick={openMetadataEditor}>
              <Pencil className="h-4 w-4 mr-2" /> Edit
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {metadataFields.length ? (
            <div className="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3">
              {metadataFields.map((field) => (
                <div
                  key={field.label}
                  className={`rounded-md border bg-white px-3 py-2 ${
                    field.wide ? "sm:col-span-2" : ""
                  }`}
                >
                  <div className="text-xs uppercase text-muted-foreground mb-1">
                    {field.label}
                  </div>
                  {field.badge ? (
                    <Badge
                      variant={priorityVariant(field.value)}
                      className="text-sm py-1 px-2"
                    >
                      {field.value}
                    </Badge>
                  ) : (
                    <div className="text-sm font-medium text-gray-900 break-words">
                      {field.value}
                    </div>
                  )}
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">
              No extracted metadata available.
            </div>
          )}
        </CardContent>
      </Card>

      <Tabs defaultValue="keypoints" className="w-full">
        <TabsList className="grid w-full grid-cols-3">
          <TabsTrigger value="keypoints">Summary</TabsTrigger>
          <TabsTrigger value="clauses">Contractual Clauses</TabsTrigger>
          <TabsTrigger value="keyreply">Key Reply Points</TabsTrigger>
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

        <TabsContent value="keyreply">
          <Card>
            <CardHeader className="flex flex-row items-center justify-between">
              <div>
                <CardTitle>Key Reply Points</CardTitle>
                <CardDescription>Points to be addressed while responding</CardDescription>
              </div>
              <Button
                size="sm"
                variant="outline"
                onClick={() => copyText(keyReplyPoints.map((s) => `• ${s}`).join("\n"))}
              >
                <Copy className="h-4 w-4 mr-2" /> Copy
              </Button>
            </CardHeader>
            <CardContent>
              {keyReplyPoints.length ? (
                <ul className="list-disc pl-5 space-y-1">
                  {keyReplyPoints.map((point: string, i: number) => (
                    <li key={i} className="text-sm break-words">
                      {point}
                    </li>
                  ))}
                </ul>
              ) : (
                <div className="text-sm text-muted-foreground">
                  No key reply points available.
                </div>
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      <Card>
        <CardHeader className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
          <div>
            <CardTitle className="text-lg flex items-center gap-2">
              <Tag className="h-4 w-4" /> Keywords
            </CardTitle>
            <CardDescription>Detected key terms and extracted tags</CardDescription>
          </div>
          {canEditMetadata && (
            <Button variant="outline" size="sm" onClick={openKeywordEditor}>
              <Pencil className="h-4 w-4 mr-2" /> Edit Keywords
            </Button>
          )}
        </CardHeader>
        <CardContent>
          {keywordGroups.length ? (
            <div className="space-y-4">
              {keywordGroups.map((group) => (
                <div key={group.label}>
                  <div className="text-xs uppercase text-muted-foreground mb-2">
                    {group.label}
                  </div>
                  <div className="flex flex-wrap gap-2">
                    {group.items.map((k: string, i: number) => (
                      <Badge
                        key={`${group.label}-${i}`}
                        variant={group.variant}
                        className="text-sm py-1 px-2"
                      >
                        {k}
                      </Badge>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="text-sm text-muted-foreground">
              No keywords or tags detected.
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

      <Dialog open={metadataDialogOpen} onOpenChange={setMetadataDialogOpen}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Edit Extracted Metadata</DialogTitle>
            <DialogDescription>
              Manual edits override the displayed AI-extracted metadata.
            </DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="summary-asset-type">Asset Type</Label>
              <Input
                id="summary-asset-type"
                value={metadataForm.asset_type}
                onChange={(event) =>
                  setMetadataForm((current) => ({
                    ...current,
                    asset_type: event.target.value,
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="summary-location">Location</Label>
              <Input
                id="summary-location"
                value={metadataForm.location}
                onChange={(event) =>
                  setMetadataForm((current) => ({
                    ...current,
                    location: event.target.value,
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="summary-work-type">Work Type</Label>
              <Input
                id="summary-work-type"
                value={metadataForm.work_type}
                onChange={(event) =>
                  setMetadataForm((current) => ({
                    ...current,
                    work_type: event.target.value,
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="summary-issue-nature">Issue Nature</Label>
              <Input
                id="summary-issue-nature"
                value={metadataForm.issue_nature}
                onChange={(event) =>
                  setMetadataForm((current) => ({
                    ...current,
                    issue_nature: event.target.value,
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="summary-claim-category">Claim Category</Label>
              <Input
                id="summary-claim-category"
                value={metadataForm.claim_category}
                onChange={(event) =>
                  setMetadataForm((current) => ({
                    ...current,
                    claim_category: event.target.value,
                  }))
                }
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="summary-responsibility">Responsibility</Label>
              <Input
                id="summary-responsibility"
                value={metadataForm.responsibility}
                onChange={(event) =>
                  setMetadataForm((current) => ({
                    ...current,
                    responsibility: event.target.value,
                  }))
                }
              />
            </div>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setMetadataDialogOpen(false)}
              disabled={savingSummaryMetadata}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={saveMetadataForm}
              disabled={savingSummaryMetadata}
            >
              {savingSummaryMetadata ? "Saving..." : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={keywordDialogOpen} onOpenChange={setKeywordDialogOpen}>
        <DialogContent className="max-h-[85vh] max-w-3xl overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Edit Keywords</DialogTitle>
            <DialogDescription>
              Add, rename, or remove terms. Empty and duplicate chips are removed on save.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-5">
            <ChipListEditor
              label="Keywords"
              items={keywordForm.keywords}
              placeholder="Add keyword"
              onChange={(items) =>
                setKeywordForm((current) => ({ ...current, keywords: items }))
              }
            />
            <ChipListEditor
              label="Additional Keywords"
              items={keywordForm.additional_keywords}
              placeholder="Add additional keyword"
              onChange={(items) =>
                setKeywordForm((current) => ({
                  ...current,
                  additional_keywords: items,
                }))
              }
            />
            <ChipListEditor
              label="Extracted Tags"
              items={keywordForm.extracted_tags}
              placeholder="Add tag"
              onChange={(items) =>
                setKeywordForm((current) => ({
                  ...current,
                  extracted_tags: items,
                }))
              }
            />
            <ChipListEditor
              label="Extracted Sub-Tags"
              items={keywordForm.extracted_subTags}
              placeholder="Add sub-tag"
              onChange={(items) =>
                setKeywordForm((current) => ({
                  ...current,
                  extracted_subTags: items,
                }))
              }
            />
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setKeywordDialogOpen(false)}
              disabled={savingSummaryMetadata}
            >
              Cancel
            </Button>
            <Button
              type="button"
              onClick={saveKeywordForm}
              disabled={savingSummaryMetadata}
            >
              {savingSummaryMetadata ? "Saving..." : "Save"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

export default LetterSummaryPage;
