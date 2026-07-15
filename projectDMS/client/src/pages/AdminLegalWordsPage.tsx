import React, { FormEvent, KeyboardEvent, useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import {
  Ban,
  CalendarClock,
  CheckCircle2,
  Edit,
  Eye,
  Loader2,
  Plus,
  Rocket,
  RefreshCw,
  Search,
  ShieldCheck,
  Sparkles,
  Trash2,
  X,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Textarea } from "@/components/ui/textarea";
import {
  LegalWord,
  LegalWordAISuggestion,
  LegalWordPayload,
  LegalWordPublicationType,
  LegalWordSource,
  LegalWordStatus,
  approveAdminLegalWord,
  createAdminLegalWord,
  deleteAdminLegalWord,
  deactivateAdminLegalWord,
  listAdminLegalWords,
  publishAdminLegalWord,
  rejectAdminLegalWord,
  scheduleAdminLegalWord,
  suggestAdminLegalWordsWithAI,
  unpublishAdminLegalWord,
  updateAdminLegalWord,
} from "@/services/legal-words-api";

type TabKey =
  | "all"
  | "pending_review"
  | "approved"
  | "scheduled"
  | "published"
  | "user_requested"
  | "closed";

type WordForm = {
  word: string;
  category: string;
  meaning: string;
  synonyms: string[];
  example_sentence: string;
  source: LegalWordSource;
  status: LegalWordStatus;
  scheduled_date: string;
  published_date: string;
};

const statusOptions: LegalWordStatus[] = [
  "pending_review",
  "approved",
  "scheduled",
  "published",
  "rejected",
  "inactive",
];

const sourceOptions: LegalWordSource[] = ["system", "admin_created", "user_requested", "ai_suggested"];

const publicationTypeOptions: LegalWordPublicationType[] = [
  "new",
  "repeated",
  "previously_published",
  "unpublished",
  "eligible_for_republication",
];

const initialForm: WordForm = {
  word: "",
  category: "",
  meaning: "",
  synonyms: [],
  example_sentence: "",
  source: "admin_created",
  status: "pending_review",
  scheduled_date: "",
  published_date: "",
};

const tabLabels: Record<TabKey, string> = {
  all: "All Words",
  pending_review: "Pending Approval",
  approved: "Approved",
  scheduled: "Scheduled",
  published: "Published",
  user_requested: "User Requested",
  closed: "Rejected/Inactive",
};

const formatLabel = (value: string) =>
  value
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");

const formatPublicationType = (value: LegalWordPublicationType) => {
  if (value === "eligible_for_republication") return "Eligible for Republication";
  if (value === "previously_published") return "Previously Published";
  return formatLabel(value);
};

const formatDate = (value?: string | null) => {
  if (!value) return "N/A";
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleDateString(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
  });
};

const normalizeChips = (values: string[]) => {
  const seen = new Set<string>();
  const next: string[] = [];
  values.forEach((value) => {
    const text = value.trim().replace(/\s+/g, " ");
    if (!text) return;
    const key = text.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    next.push(text);
  });
  return next;
};

const AdminLegalWordsPage = () => {
  const [activeTab, setActiveTab] = useState<TabKey>("all");
  const [words, setWords] = useState<LegalWord[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [suggesting, setSuggesting] = useState(false);
  const [suggestions, setSuggestions] = useState<LegalWordAISuggestion[]>([]);
  const [search, setSearch] = useState("");
  const [appliedSearch, setAppliedSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState<"all" | LegalWordStatus>("all");
  const [sourceFilter, setSourceFilter] = useState<"all" | LegalWordSource>("all");
  const [categoryFilter, setCategoryFilter] = useState("");
  const [publishedDateFilter, setPublishedDateFilter] = useState("");
  const [publicationTypeFilter, setPublicationTypeFilter] = useState<"all" | LegalWordPublicationType>("all");
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editingWord, setEditingWord] = useState<LegalWord | null>(null);
  const [previewWord, setPreviewWord] = useState<LegalWord | null>(null);
  const [form, setForm] = useState<WordForm>(initialForm);
  const [scheduleWord, setScheduleWord] = useState<LegalWord | null>(null);
  const [scheduleDate, setScheduleDate] = useState("");

  const loadWords = useCallback(async () => {
    setLoading(true);
    try {
      const response = await listAdminLegalWords({
        limit: 200,
        status: statusFilter === "all" ? undefined : statusFilter,
        source: sourceFilter === "all" ? undefined : sourceFilter,
        category: categoryFilter.trim() || undefined,
        published_date: publishedDateFilter || undefined,
        publication_type: publicationTypeFilter === "all" ? undefined : publicationTypeFilter,
        search: appliedSearch || undefined,
      });
      setWords(response.words);
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || "Failed to load legal words");
    } finally {
      setLoading(false);
    }
  }, [appliedSearch, categoryFilter, publishedDateFilter, publicationTypeFilter, sourceFilter, statusFilter]);

  useEffect(() => {
    void loadWords();
  }, [loadWords]);

  const displayedWords = useMemo(() => {
    if (activeTab === "all") return words;
    if (activeTab === "user_requested") {
      return words.filter((word) => word.source === "user_requested" && word.status === "pending_review");
    }
    if (activeTab !== "closed") return words.filter((word) => word.status === activeTab);
    return words.filter((word) => word.status === "rejected" || word.status === "inactive");
  }, [activeTab, words]);

  const counts = useMemo(() => {
    const base = {
      all: words.length,
      pending_review: 0,
      approved: 0,
      scheduled: 0,
      published: 0,
      user_requested: 0,
      closed: 0,
    };
    words.forEach((word) => {
      if (word.status in base) base[word.status as keyof typeof base] += 1;
      if (word.source === "user_requested" && word.status === "pending_review") {
        base.user_requested += 1;
      }
      if (word.status === "rejected" || word.status === "inactive") base.closed += 1;
    });
    return base;
  }, [words]);

  const categories = useMemo(
    () =>
      Array.from(new Set(words.map((word) => word.category).filter(Boolean) as string[]))
        .sort((a, b) => a.localeCompare(b)),
    [words],
  );

  const filtersActive =
    statusFilter !== "all" ||
    sourceFilter !== "all" ||
    !!categoryFilter ||
    !!publishedDateFilter ||
    publicationTypeFilter !== "all" ||
    !!appliedSearch;

  const clearFilters = () => {
    setStatusFilter("all");
    setSourceFilter("all");
    setCategoryFilter("");
    setPublishedDateFilter("");
    setPublicationTypeFilter("all");
    setSearch("");
    setAppliedSearch("");
  };

  const openCreate = () => {
    setEditingWord(null);
    setForm(initialForm);
    setDialogOpen(true);
  };

  const openEdit = (word: LegalWord) => {
    setEditingWord(word);
    setForm({
      word: word.word || "",
      category: word.category || "",
      meaning: word.meaning || "",
      synonyms: normalizeChips(word.synonyms || []),
      example_sentence: word.example_sentence || "",
      source: word.source || "admin_created",
      status: word.status || "pending_review",
      scheduled_date: word.scheduled_date || "",
      published_date: word.published_date || "",
    });
    setDialogOpen(true);
  };

  const findSuggestionRecord = (suggestion: LegalWordAISuggestion) =>
    suggestion.word_record ||
    words.find((word) => word.id === suggestion.existing_word_id) ||
    null;

  const openSuggestionEdit = (suggestion: LegalWordAISuggestion) => {
    const record = findSuggestionRecord(suggestion);
    if (!record) {
      toast.error("Suggestion record is not available yet");
      return;
    }
    setEditingWord(record);
    setForm({
      word: suggestion.word || record.word || "",
      category: record.category || "",
      meaning: suggestion.meaning || record.meaning || "",
      synonyms: normalizeChips(suggestion.synonyms?.length ? suggestion.synonyms : record.synonyms || []),
      example_sentence: suggestion.example_sentence || record.example_sentence || "",
      source: record.source || "ai_suggested",
      status: record.status || "pending_review",
      scheduled_date: record.scheduled_date || "",
      published_date: record.published_date || "",
    });
    setDialogOpen(true);
  };

  const suggestWordsWithAI = async () => {
    setSuggesting(true);
    try {
      const response = await suggestAdminLegalWordsWithAI();
      setSuggestions(response.suggestions);
      toast.success(`AI suggested ${response.count} legal words`);
      await loadWords();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || "Failed to suggest words with AI");
    } finally {
      setSuggesting(false);
    }
  };

  const setField = <K extends keyof WordForm>(key: K, value: WordForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
  };

  const submitSearch = (event: FormEvent) => {
    event.preventDefault();
    setAppliedSearch(search.trim());
  };

  const saveWord = async () => {
    const payload: LegalWordPayload = {
      word: form.word.trim(),
      category: form.category.trim() || null,
      meaning: form.meaning.trim() || null,
      synonyms: normalizeChips(form.synonyms),
      example_sentence: form.example_sentence.trim() || null,
      source: form.source,
      status: form.status,
      scheduled_date: form.scheduled_date || null,
      published_date: form.published_date || null,
    };
    if (!payload.word || !payload.meaning || !payload.example_sentence) {
      toast.error("Word, meaning, and example sentence are required");
      return;
    }

    setSaving(true);
    try {
      if (editingWord) {
        await updateAdminLegalWord(editingWord.id, payload);
        toast.success("Word updated");
      } else {
        await createAdminLegalWord(payload);
        toast.success("Word created");
      }
      setDialogOpen(false);
      await loadWords();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || "Failed to save word");
    } finally {
      setSaving(false);
    }
  };

  const runAction = async (label: string, action: () => Promise<unknown>) => {
    setSaving(true);
    try {
      await action();
      toast.success(label);
      await loadWords();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || "Action failed");
    } finally {
      setSaving(false);
    }
  };

  const openSchedule = (word: LegalWord) => {
    setScheduleWord(word);
    setScheduleDate(word.scheduled_date || "");
  };

  const canPublishDirectly = (word: LegalWord) =>
    word.is_eligible_for_republish !== false &&
    (word.status === "approved" || word.status === "scheduled" || word.status === "published");

  const saveSchedule = async () => {
    if (!scheduleWord || !scheduleDate) {
      toast.error("Select a schedule date");
      return;
    }
    setSaving(true);
    try {
      await scheduleAdminLegalWord(scheduleWord.id, scheduleDate);
      toast.success("Word scheduled");
      setScheduleWord(null);
      await loadWords();
    } catch (error: any) {
      toast.error(error?.response?.data?.detail || error?.message || "Failed to schedule word");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="mx-auto flex max-w-7xl flex-col gap-6 p-4 md:p-6">
      <header className="flex flex-col gap-4 border-b border-gray-200 pb-5 md:flex-row md:items-end md:justify-between">
        <div className="flex items-start gap-3">
          <div className="mt-1 rounded-md bg-blue-50 p-2 text-blue-700">
            <ShieldCheck className="h-6 w-6" />
          </div>
          <div>
            <h1 className="text-2xl font-semibold text-gray-950">Legal Words Admin</h1>
            <p className="mt-1 text-sm text-gray-600">
              Manage contractual/legal words, approvals, requests, and publication dates.
            </p>
          </div>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button variant="outline" onClick={() => void loadWords()} disabled={loading}>
            {loading ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <RefreshCw className="mr-2 h-4 w-4" />}
            Refresh
          </Button>
          <Button variant="outline" onClick={() => void suggestWordsWithAI()} disabled={suggesting}>
            {suggesting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Sparkles className="mr-2 h-4 w-4" />}
            Suggest Words with AI
          </Button>
          <Button onClick={openCreate}>
            <Plus className="mr-2 h-4 w-4" />
            New Word
          </Button>
        </div>
      </header>

      <div className="space-y-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center lg:justify-between">
          <Tabs value={activeTab} onValueChange={(value) => setActiveTab(value as TabKey)}>
            <TabsList className="flex h-auto flex-wrap justify-start gap-1">
              {(Object.keys(tabLabels) as TabKey[]).map((tab) => (
                <TabsTrigger key={tab} value={tab} className="rounded-md">
                  {tabLabels[tab]} ({counts[tab]})
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <form onSubmit={submitSearch} className="flex min-w-0 gap-2 lg:w-80">
            <Input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              placeholder="Search words or meanings"
            />
            <Button type="submit" variant="outline" size="icon" aria-label="Search">
              <Search className="h-4 w-4" />
            </Button>
          </form>
        </div>

        <div className="grid gap-3 rounded-md border border-gray-200 bg-white p-3 md:grid-cols-2 xl:grid-cols-6">
          <Field label="Status">
            <Select value={statusFilter} onValueChange={(value) => setStatusFilter(value as "all" | LegalWordStatus)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Statuses</SelectItem>
                {statusOptions.map((status) => (
                  <SelectItem key={status} value={status}>{formatLabel(status)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Source">
            <Select value={sourceFilter} onValueChange={(value) => setSourceFilter(value as "all" | LegalWordSource)}>
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Sources</SelectItem>
                {sourceOptions.map((source) => (
                  <SelectItem key={source} value={source}>{formatLabel(source)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <Field label="Category">
            <Input
              list="legal-word-categories"
              value={categoryFilter}
              onChange={(event) => setCategoryFilter(event.target.value)}
              placeholder="All categories"
            />
            <datalist id="legal-word-categories">
              {categories.map((category) => (
                <option key={category} value={category} />
              ))}
            </datalist>
          </Field>
          <Field label="Publication Date">
            <Input
              type="date"
              value={publishedDateFilter}
              onChange={(event) => setPublishedDateFilter(event.target.value)}
            />
          </Field>
          <Field label="Publication Type">
            <Select
              value={publicationTypeFilter}
              onValueChange={(value) => setPublicationTypeFilter(value as "all" | LegalWordPublicationType)}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All Types</SelectItem>
                {publicationTypeOptions.map((type) => (
                  <SelectItem key={type} value={type}>{formatPublicationType(type)}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </Field>
          <div className="flex items-end">
            <Button type="button" variant="outline" onClick={clearFilters} disabled={!filtersActive} className="w-full">
              Clear Filters
            </Button>
          </div>
        </div>

        {suggestions.length ? (
          <AISuggestionsTable
            suggestions={suggestions}
            saving={saving}
            findRecord={findSuggestionRecord}
            onEdit={openSuggestionEdit}
            onApprove={(suggestion) => {
              const record = findSuggestionRecord(suggestion);
              if (!record) {
                toast.error("Suggestion record is not available yet");
                return;
              }
              void runAction("Word approved", () => approveAdminLegalWord(record.id));
            }}
            onReject={(suggestion) => {
              const record = findSuggestionRecord(suggestion);
              if (!record) {
                toast.error("Suggestion record is not available yet");
                return;
              }
              void runAction("Word rejected", () => rejectAdminLegalWord(record.id));
            }}
            onDeactivate={(suggestion) => {
              const record = findSuggestionRecord(suggestion);
              if (!record) {
                toast.error("Suggestion record is not available yet");
                return;
              }
              void runAction("Word deactivated", () => deactivateAdminLegalWord(record.id));
            }}
            onSchedule={(suggestion) => {
              const record = findSuggestionRecord(suggestion);
              if (!record) {
                toast.error("Suggestion record is not available yet");
                return;
              }
              openSchedule(record);
            }}
            onPublish={(suggestion) => {
              const record = findSuggestionRecord(suggestion);
              if (!record) {
                toast.error("Suggestion record is not available yet");
                return;
              }
              void runAction("Word published for today", () => publishAdminLegalWord(record.id));
            }}
          />
        ) : null}

        <div className="overflow-hidden rounded-md border border-gray-200 bg-white">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-[18%]">Word</TableHead>
                <TableHead>Meaning</TableHead>
                <TableHead className="w-[12%]">Category</TableHead>
                <TableHead className="w-[12%]">Status</TableHead>
                <TableHead className="w-[16%]">Publication</TableHead>
                <TableHead className="w-[14%]">Dates</TableHead>
                <TableHead className="w-[300px] text-right">Actions</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {loading ? (
                <TableRow>
                  <TableCell colSpan={7} className="h-32 text-center text-gray-500">
                    <Loader2 className="mx-auto mb-2 h-5 w-5 animate-spin" />
                    Loading legal words
                  </TableCell>
                </TableRow>
              ) : displayedWords.length ? (
                displayedWords.map((word) => (
                  <TableRow key={word.id}>
                    <TableCell className="align-top">
                      <div className="font-medium text-gray-950">{word.word}</div>
                      <div className="mt-1 text-xs text-gray-500">{formatLabel(word.source)}</div>
                      <div className="mt-1 flex flex-wrap gap-1">
                        {word.synonyms.slice(0, 3).map((synonym) => (
                          <Badge key={synonym} variant="outline" className="rounded-md bg-gray-50 font-normal">
                            {synonym}
                          </Badge>
                        ))}
                        {word.synonyms.length > 3 ? (
                          <Badge variant="outline" className="rounded-md bg-gray-50 font-normal">
                            +{word.synonyms.length - 3}
                          </Badge>
                        ) : null}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-xl align-top">
                      <p className="line-clamp-2 text-sm text-gray-700">{word.meaning || "Meaning pending"}</p>
                      <p className="mt-1 line-clamp-2 text-xs text-gray-500">
                        {word.example_sentence || "Example pending"}
                      </p>
                    </TableCell>
                    <TableCell className="align-top">
                      {word.category ? (
                        <Badge variant="outline" className="rounded-md bg-gray-50">
                          {word.category}
                        </Badge>
                      ) : (
                        <span className="text-xs text-gray-500">Uncategorised</span>
                      )}
                    </TableCell>
                    <TableCell className="align-top">
                      <StatusBadge word={word} />
                    </TableCell>
                    <TableCell className="align-top">
                      <PublicationTypeBadge word={word} />
                      <div className="mt-1 text-xs text-gray-500">
                        {word.published_count || 0} publication{(word.published_count || 0) === 1 ? "" : "s"}
                      </div>
                      {word.is_eligible_for_republish === false ? (
                        <div className="mt-1 text-xs text-red-700">Repeat blocked until period passes</div>
                      ) : null}
                    </TableCell>
                    <TableCell className="align-top text-xs text-gray-600">
                      <div>Scheduled: {formatDate(word.scheduled_date)}</div>
                      <div className="mt-1">Last: {formatDate(word.last_published_date || word.published_date)}</div>
                    </TableCell>
                    <TableCell className="align-top">
                      <div className="flex flex-wrap justify-end gap-1">
                        <IconButton label="Preview" onClick={() => setPreviewWord(word)}>
                          <Eye className="h-4 w-4" />
                        </IconButton>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          className="h-8 gap-1 px-2"
                          disabled={saving || !canPublishDirectly(word)}
                          onClick={() =>
                            void runAction("Word published for today", () => publishAdminLegalWord(word.id))
                          }
                        >
                          <Rocket className="h-4 w-4" />
                          Publish
                        </Button>
                        <IconButton label="Edit" onClick={() => openEdit(word)}>
                          <Edit className="h-4 w-4" />
                        </IconButton>
                        <IconButton
                          label="Approve"
                          onClick={() => void runAction("Word approved", () => approveAdminLegalWord(word.id))}
                          disabled={word.status === "approved" || word.status === "published"}
                        >
                          <CheckCircle2 className="h-4 w-4" />
                        </IconButton>
                        <IconButton
                          label="Schedule"
                          onClick={() => openSchedule(word)}
                          disabled={word.status !== "approved" && word.status !== "scheduled"}
                        >
                          <CalendarClock className="h-4 w-4" />
                        </IconButton>
                        <IconButton
                          label="Reject"
                          onClick={() => void runAction("Word rejected", () => rejectAdminLegalWord(word.id))}
                          disabled={word.status === "rejected"}
                        >
                          <XCircle className="h-4 w-4" />
                        </IconButton>
                        <IconButton
                          label="Deactivate"
                          onClick={() => void runAction("Word deactivated", () => deactivateAdminLegalWord(word.id))}
                          disabled={word.status === "inactive"}
                        >
                          <Ban className="h-4 w-4" />
                        </IconButton>
                        <IconButton
                          label="Unpublish"
                          onClick={() => void runAction("Word unpublished", () => unpublishAdminLegalWord(word.id))}
                          disabled={!word.published_count}
                        >
                          <X className="h-4 w-4" />
                        </IconButton>
                        <IconButton
                          label="Delete"
                          onClick={() => {
                            if (window.confirm(`Delete legal word "${word.word}"? This cannot be undone.`)) {
                              void runAction("Word deleted", () => deleteAdminLegalWord(word.id));
                            }
                          }}
                        >
                          <Trash2 className="h-4 w-4 text-red-600" />
                        </IconButton>
                      </div>
                    </TableCell>
                  </TableRow>
                ))
              ) : (
                <TableRow>
                  <TableCell colSpan={7} className="h-32 text-center text-sm text-gray-500">
                    No legal words match this view.
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>
      </div>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-3xl">
          <DialogHeader>
            <DialogTitle>{editingWord ? "Edit Legal Word" : "Create Legal Word"}</DialogTitle>
            <DialogDescription>
              Word records must have a meaning and example before approval or scheduling.
            </DialogDescription>
          </DialogHeader>

          <div className="grid gap-4 py-2">
            <div className="grid gap-4 md:grid-cols-2">
              <Field label="Word">
                <Input value={form.word} onChange={(event) => setField("word", event.target.value)} />
              </Field>
              <Field label="Category">
                <Input
                  value={form.category}
                  onChange={(event) => setField("category", event.target.value)}
                  placeholder="e.g. Contractual, Claims, Dispute"
                />
              </Field>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Source">
                  <Select value={form.source} onValueChange={(value) => setField("source", value as LegalWordSource)}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {sourceOptions.map((source) => (
                        <SelectItem key={source} value={source}>{formatLabel(source)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
                <Field label="Status">
                  <Select value={form.status} onValueChange={(value) => setField("status", value as LegalWordStatus)}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {statusOptions.map((status) => (
                        <SelectItem key={status} value={status}>{formatLabel(status)}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </Field>
            </div>

            <Field label="Meaning">
              <Textarea
                rows={3}
                value={form.meaning}
                onChange={(event) => setField("meaning", event.target.value)}
              />
            </Field>

            <Field label="Useful Synonyms">
              <ChipEditor
                values={form.synonyms}
                onChange={(values) => setField("synonyms", values)}
                placeholder="Add synonym"
              />
            </Field>

            <Field label="Example in Contractual Letter">
              <Textarea
                rows={4}
                value={form.example_sentence}
                onChange={(event) => setField("example_sentence", event.target.value)}
              />
            </Field>

            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Scheduled Date">
                <Input
                  type="date"
                  value={form.scheduled_date}
                  onChange={(event) => setField("scheduled_date", event.target.value)}
                />
              </Field>
              <Field label="Published Date">
                <Input
                  type="date"
                  value={form.published_date}
                  onChange={(event) => setField("published_date", event.target.value)}
                />
              </Field>
            </div>
          </div>

          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogOpen(false)}>Cancel</Button>
            <Button onClick={saveWord} disabled={saving}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!previewWord} onOpenChange={(open) => !open && setPreviewWord(null)}>
        <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>{previewWord?.word || "Legal Word Preview"}</DialogTitle>
            <DialogDescription>
              Preview the learner-facing content and review publication history.
            </DialogDescription>
          </DialogHeader>
          {previewWord ? (
            <div className="space-y-4">
              <div className="flex flex-wrap gap-2">
                <StatusBadge word={previewWord} />
                <PublicationTypeBadge word={previewWord} />
                {previewWord.category ? (
                  <Badge variant="outline" className="rounded-md">{previewWord.category}</Badge>
                ) : null}
                <Badge variant="outline" className="rounded-md">{formatLabel(previewWord.source)}</Badge>
              </div>
              <div>
                <div className="mb-1 text-xs font-semibold uppercase text-gray-500">Meaning</div>
                <p className="leading-6 text-gray-800">{previewWord.meaning || "Meaning pending."}</p>
              </div>
              <div>
                <div className="mb-1 text-xs font-semibold uppercase text-gray-500">Useful Synonyms</div>
                {previewWord.synonyms.length ? (
                  <div className="flex flex-wrap gap-2">
                    {previewWord.synonyms.map((synonym) => (
                      <Badge key={synonym} variant="outline" className="rounded-md bg-gray-50">
                        {synonym}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-gray-500">No synonyms recorded.</p>
                )}
              </div>
              <div>
                <div className="mb-1 text-xs font-semibold uppercase text-gray-500">Example in Contractual Letter</div>
                <p className="rounded-md border border-gray-200 bg-gray-50 p-3 leading-6 text-gray-800">
                  {previewWord.example_sentence || "Example pending."}
                </p>
              </div>
              <div>
                <div className="mb-2 text-xs font-semibold uppercase text-gray-500">Publication / Republication History</div>
                {previewWord.publication_history?.length ? (
                  <div className="space-y-2">
                    {previewWord.publication_history.map((item, index) => (
                      <div key={`${item}-${index}`} className="flex items-center justify-between rounded-md border border-gray-200 px-3 py-2 text-sm">
                        <span>{formatDate(item)}</span>
                        <Badge variant="secondary" className="rounded-md">
                          {index === 0 ? "First Publication" : "Republication"}
                        </Badge>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-gray-500">This word has not been published yet.</p>
                )}
              </div>
            </div>
          ) : null}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPreviewWord(null)}>Close</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!scheduleWord} onOpenChange={(open) => !open && setScheduleWord(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Schedule Word</DialogTitle>
            <DialogDescription>
              Select the publication date for {scheduleWord?.word || "this word"}.
            </DialogDescription>
          </DialogHeader>
          <Field label="Scheduled Date">
            <Input type="date" value={scheduleDate} onChange={(event) => setScheduleDate(event.target.value)} />
          </Field>
          <DialogFooter>
            <Button variant="outline" onClick={() => setScheduleWord(null)}>Cancel</Button>
            <Button onClick={saveSchedule} disabled={saving || !scheduleDate}>
              {saving ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
              Schedule
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
};

const AISuggestionsTable = ({
  suggestions,
  saving,
  findRecord,
  onEdit,
  onApprove,
  onReject,
  onDeactivate,
  onSchedule,
  onPublish,
}: {
  suggestions: LegalWordAISuggestion[];
  saving: boolean;
  findRecord: (suggestion: LegalWordAISuggestion) => LegalWord | null;
  onEdit: (suggestion: LegalWordAISuggestion) => void;
  onApprove: (suggestion: LegalWordAISuggestion) => void;
  onReject: (suggestion: LegalWordAISuggestion) => void;
  onDeactivate: (suggestion: LegalWordAISuggestion) => void;
  onSchedule: (suggestion: LegalWordAISuggestion) => void;
  onPublish: (suggestion: LegalWordAISuggestion) => void;
}) => (
  <section className="overflow-hidden rounded-md border border-blue-200 bg-white">
    <div className="flex flex-col gap-2 border-b border-blue-100 bg-blue-50 px-4 py-3 md:flex-row md:items-center md:justify-between">
      <div className="flex items-center gap-2">
        <Sparkles className="h-5 w-5 text-blue-700" />
        <h2 className="text-base font-semibold text-gray-950">AI Suggestions</h2>
      </div>
      <p className="text-sm text-gray-600">
        Review eligibility before approving, scheduling, or publishing.
      </p>
    </div>
    <div className="overflow-x-auto">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="min-w-40">Word</TableHead>
            <TableHead className="min-w-64">Meaning</TableHead>
            <TableHead className="min-w-48">Useful Synonyms</TableHead>
            <TableHead className="min-w-72">Example in Contractual Letter</TableHead>
            <TableHead className="min-w-48">Previous Publication Status</TableHead>
            <TableHead className="min-w-36">Last Published Date</TableHead>
            <TableHead className="min-w-56">Eligibility Status</TableHead>
            <TableHead className="min-w-56 text-right">Admin Action</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {suggestions.map((suggestion) => {
            const record = findRecord(suggestion);
            const eligible = suggestion.is_eligible_for_republish;
            const recordComplete = !!record?.meaning && !!record?.example_sentence;
            const canApprove =
              !!record &&
              eligible &&
              recordComplete &&
              record.status !== "approved" &&
              record.status !== "published";
            const canSchedule =
              !!record &&
              eligible &&
              (record.status === "approved" ||
                record.status === "scheduled" ||
                record.status === "published");
            const canPublish = canSchedule;
            return (
              <TableRow key={`${suggestion.word}-${suggestion.eligibility_status}`}>
                <TableCell className="align-top">
                  <div className="font-medium text-gray-950">{suggestion.word}</div>
                  {record ? (
                    <div className="mt-1 text-xs text-gray-500">
                      Record: {formatLabel(record.status)}
                    </div>
                  ) : null}
                </TableCell>
                <TableCell className="max-w-md align-top text-sm text-gray-700">
                  {suggestion.meaning}
                </TableCell>
                <TableCell className="align-top">
                  <div className="flex flex-wrap gap-1">
                    {suggestion.synonyms.length ? (
                      suggestion.synonyms.map((synonym) => (
                        <Badge key={synonym} variant="outline" className="rounded-md bg-gray-50 font-normal">
                          {synonym}
                        </Badge>
                      ))
                    ) : (
                      <span className="text-sm text-gray-500">None</span>
                    )}
                  </div>
                </TableCell>
                <TableCell className="max-w-lg align-top text-sm text-gray-700">
                  {suggestion.example_sentence}
                </TableCell>
                <TableCell className="align-top text-sm text-gray-700">
                  {suggestion.previous_publication_status}
                </TableCell>
                <TableCell className="align-top text-sm text-gray-700">
                  {formatDate(suggestion.last_published_date)}
                </TableCell>
                <TableCell className="align-top">
                  <EligibilityBadge suggestion={suggestion} />
                  {suggestion.blocked_reason ? (
                    <p className="mt-1 text-xs text-red-700">{suggestion.blocked_reason}</p>
                  ) : null}
                </TableCell>
                <TableCell className="align-top">
                  <div className="flex justify-end gap-1">
                    <IconButton label="Edit" onClick={() => onEdit(suggestion)} disabled={saving || !record}>
                      <Edit className="h-4 w-4" />
                    </IconButton>
                    <IconButton
                      label="Approve"
                      onClick={() => onApprove(suggestion)}
                      disabled={saving || !canApprove}
                    >
                      <CheckCircle2 className="h-4 w-4" />
                    </IconButton>
                    <IconButton
                      label="Schedule"
                      onClick={() => onSchedule(suggestion)}
                      disabled={saving || !canSchedule}
                    >
                      <CalendarClock className="h-4 w-4" />
                    </IconButton>
                    <IconButton
                      label="Publish immediately"
                      onClick={() => onPublish(suggestion)}
                      disabled={saving || !canPublish}
                    >
                      <Rocket className="h-4 w-4" />
                    </IconButton>
                    <IconButton
                      label="Reject"
                      onClick={() => onReject(suggestion)}
                      disabled={saving || !record || record.status === "rejected"}
                    >
                      <XCircle className="h-4 w-4" />
                    </IconButton>
                    <IconButton
                      label="Deactivate"
                      onClick={() => onDeactivate(suggestion)}
                      disabled={saving || !record || record.status === "inactive"}
                    >
                      <Ban className="h-4 w-4" />
                    </IconButton>
                  </div>
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  </section>
);

const EligibilityBadge = ({ suggestion }: { suggestion: LegalWordAISuggestion }) => {
  const className =
    suggestion.eligibility_status === "blocked_recently_published"
      ? "bg-red-100 text-red-800"
      : suggestion.eligibility_status === "new_word"
        ? "bg-green-100 text-green-800"
        : "bg-blue-100 text-blue-800";
  return (
    <Badge variant="secondary" className={`rounded-md ${className}`}>
      {suggestion.eligibility_label}
    </Badge>
  );
};

const Field = ({ label, children }: { label: string; children: React.ReactNode }) => (
  <div className="grid gap-1.5">
    <Label className="text-xs font-semibold uppercase text-gray-500">{label}</Label>
    {children}
  </div>
);

const ChipEditor = ({
  values,
  onChange,
  placeholder,
}: {
  values: string[];
  onChange: (values: string[]) => void;
  placeholder: string;
}) => {
  const [draft, setDraft] = useState("");

  const addDraft = () => {
    const parts = draft.split(",").map((part) => part.trim());
    const next = normalizeChips([...values, ...parts]);
    onChange(next);
    setDraft("");
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "Enter" || event.key === ",") {
      event.preventDefault();
      addDraft();
    } else if (event.key === "Backspace" && !draft && values.length) {
      onChange(values.slice(0, -1));
    }
  };

  const remove = (index: number) => {
    onChange(values.filter((_, current) => current !== index));
  };

  return (
    <div className="rounded-md border border-input bg-background px-2 py-2">
      <div className="flex flex-wrap gap-2">
        {values.map((value, index) => (
          <Badge key={`${value}-${index}`} variant="secondary" className="rounded-md">
            {value}
            <button
              type="button"
              onClick={() => remove(index)}
              className="ml-1 rounded-sm text-gray-500 hover:text-gray-900"
              aria-label={`Remove ${value}`}
            >
              <X className="h-3 w-3" />
            </button>
          </Badge>
        ))}
        <input
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onBlur={addDraft}
          onKeyDown={onKeyDown}
          placeholder={values.length ? "" : placeholder}
          className="min-h-7 min-w-32 flex-1 bg-transparent text-sm outline-none"
        />
      </div>
    </div>
  );
};

const publicationTypeForWord = (word: LegalWord): LegalWordPublicationType => {
  const count = Number(word.published_count || 0);
  if (count <= 0) return "unpublished";
  if (word.is_eligible_for_republish) return "eligible_for_republication";
  if (count > 1) return "repeated";
  return "previously_published";
};

const PublicationTypeBadge = ({ word }: { word: LegalWord }) => {
  const type = publicationTypeForWord(word);
  const className =
    type === "repeated"
      ? "bg-amber-100 text-amber-800"
      : type === "unpublished"
        ? "bg-gray-100 text-gray-700"
        : type === "eligible_for_republication"
          ? "bg-blue-100 text-blue-800"
          : "bg-green-100 text-green-800";
  return (
    <Badge variant="secondary" className={`rounded-md ${className}`}>
      {formatPublicationType(type)}
    </Badge>
  );
};

const StatusBadge = ({ word }: { word: LegalWord }) => {
  const status = word.status;
  const label =
    status === "pending_review"
      ? "Draft"
      : status === "inactive" || status === "rejected"
        ? "Unpublished"
        : status === "published" && Number(word.published_count || 0) > 1
          ? "Repeated"
          : formatLabel(status);
  const className =
    status === "published"
      ? "bg-green-100 text-green-800"
      : status === "approved" || status === "scheduled"
        ? "bg-blue-100 text-blue-800"
        : status === "pending_review"
          ? "bg-amber-100 text-amber-800"
          : "bg-gray-100 text-gray-700";
  return (
    <Badge variant="secondary" className={`rounded-md ${className}`}>
      {label}
    </Badge>
  );
};

const IconButton = ({
  label,
  disabled,
  onClick,
  children,
}: {
  label: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) => (
  <Button
    type="button"
    variant="ghost"
    size="icon"
    className="h-8 w-8"
    title={label}
    aria-label={label}
    disabled={disabled}
    onClick={onClick}
  >
    {children}
  </Button>
);

export default AdminLegalWordsPage;
