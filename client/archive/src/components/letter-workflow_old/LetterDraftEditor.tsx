import React, { useEffect, useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import ReactQuill from "react-quill";
import "react-quill/dist/quill.snow.css";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Loader2,
  Plus,
  Search,
  Sparkles,
  X,
  MessageCircleQuestion,
  FileSearch,
} from "lucide-react";

import { format } from "date-fns";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "sonner";
import AIAssistant from "./AIAssistant";
import DeepPlanningAssistant from "./DeepPlanningAssistant";

import { api } from "@/services/api";
import type { Letter } from "./types";
import RequestInputForm from "./RequestInputForm";

// UI reference shape used by Letter types (UI layer)
type UIReference = {
  id: string;
  title: string;
  subject: string;
  date: string;
  referenceNumber: string;
};

// Backend letter shape (subset) used for reference lookup mapping
interface ReferenceLetterResponse {
  id: string;
  title: string;
  subject: string;
  recipient: string;
  created_at?: string;
  status: string;
  reference?: {
    id: string;
    title: string;
    subject: string;
    date: string;
    reference_number: string;
  } | null;
  reference_number?: string;
}

// Mapped minimal letter for reference picker
interface MappedReferenceLetter {
  id: string;
  title: string;
  subject: string;
  recipient: string;
  referenceNumber: string;
  createdAt: string;
  status: string;
}

interface LetterDraftEditorProps {
  letter: Letter; // UI Letter type
  onSave: (updatedLetter: Letter) => void;
  onCancel: () => void;
}

export default function LetterDraftEditor({
  letter,
  onSave,
  onCancel,
}: LetterDraftEditorProps) {
  const [content, setContent] = useState<string>((letter as any).content ?? "");
  const [reference, setReference] = useState<UIReference | undefined>(
    (letter as any).reference
      ? {
          id: (letter as any).reference.id,
          title: (letter as any).reference.title,
          subject: (letter as any).reference.subject,
          date: (letter as any).reference.date,
          referenceNumber:
            (letter as any).reference.referenceNumber ??
            (letter as any).reference.reference_number,
        }
      : undefined
  );

  const [isReferenceDialogOpen, setIsReferenceDialogOpen] = useState(false);
  const [searchQuery, setSearchQuery] = useState("");

  const letterNumber =
    (letter as any).letter_no ??
    (letter as any).reference?.referenceNumber ??
    (letter as any).reference?.reference_number ??
    "New Draft";

  const [previousLetters, setPreviousLetters] = useState<
    MappedReferenceLetter[]
  >([]);
  const [isLoadingReferences, setIsLoadingReferences] = useState(false);
  const [referenceError, setReferenceError] = useState<string | null>(null);

  // Seek Input dialog from draft editor
  const [isSeekDialogOpen, setIsSeekDialogOpen] = useState(false);
  const [preselectedLine, setPreselectedLine] = useState<string>("");
  const [users, setUsers] = useState<
    { id: string; name: string; email: string; avatar?: string }[]
  >([]);

  const quillModules = useMemo(
    () => ({
      toolbar: [
        [{ header: [1, 2, 3, false] }],
        ["bold", "italic", "underline", "strike", "blockquote"],
        [{ list: "ordered" }, { list: "bullet" }],
        ["link"],
        ["clean"],
      ],
    }),
    []
  );

  const quillFormats = useMemo(
    () => [
      "header",
      "bold",
      "italic",
      "underline",
      "strike",
      "blockquote",
      "list",
      "bullet",
      "link",
    ],
    []
  );

  // Fetch reference candidates from backend, server filters by project_id and q
  const fetchReferences = async (search?: string) => {
    setIsLoadingReferences(true);
    setReferenceError(null);
    try {
      const params: Record<string, any> = {
        project_id: (letter as any).projectId ?? (letter as any).project_id,
        ...(search ? { q: search } : {}),
        limit: 50,
      };
      const { data } = await api.get<ReferenceLetterResponse[]>("/letters", {
        params,
      });
      const mapped: MappedReferenceLetter[] = (data || []).map((l: any) => ({
        id: String(l.id ?? l._id ?? ""),
        title: l.title,
        subject: l.subject,
        recipient: l.recipient,
        referenceNumber:
          l?.reference?.reference_number ?? l?.reference_number ?? String(l.id),
        createdAt: l.created_at ?? new Date().toISOString(),
        status: l.status,
      }));
      setPreviousLetters(mapped);
    } catch (err) {
      console.error("Failed to load references", err);
      setReferenceError("Failed to load reference letters");
      setPreviousLetters([]);
      toast.error("Failed to load reference letters");
    } finally {
      setIsLoadingReferences(false);
    }
  };

  // Load users for RequestInputForm (local fetch so editor can open request dialog independently)
  useEffect(() => {
    const loadUsers = async () => {
      try {
        const { data } = await api.get<any[]>("/users");
        const mapped =
          (Array.isArray(data) ? data : []).map((u: any) => ({
            id: u.id ?? u._id ?? String(u.email ?? ""),
            name: u.username ?? u.name ?? u.full_name ?? u.email ?? "User",
            email: u.email ?? "",
            avatar: u.avatar ?? undefined,
          })) ?? [];
        setUsers(mapped);
      } catch (e) {
        setUsers([]);
      }
    };
    loadUsers();
  }, []);

  // Initial load when dialog opens
  useEffect(() => {
    if (isReferenceDialogOpen) {
      fetchReferences(searchQuery);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isReferenceDialogOpen]);

  // Debounced search
  useEffect(() => {
    if (!isReferenceDialogOpen) return;
    const t = setTimeout(() => fetchReferences(searchQuery), 300);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [searchQuery, isReferenceDialogOpen]);

  // Client-side safety filter
  const filteredLetters = previousLetters.filter(
    (l) =>
      (l.title || "").toLowerCase().includes(searchQuery.toLowerCase()) ||
      (l.subject || "").toLowerCase().includes(searchQuery.toLowerCase()) ||
      (l.referenceNumber || "")
        .toLowerCase()
        .includes(searchQuery.toLowerCase()) ||
      (l.recipient || "").toLowerCase().includes(searchQuery.toLowerCase())
  );

  const handleSubmitForReview = async () => {
    try {
      // Persist current content and selected reference before submitting
      const refPayload = reference
        ? {
            id: reference.id,
            title: reference.title,
            subject: reference.subject,
            date: reference.date,
            reference_number: reference.referenceNumber,
          }
        : undefined;

      const hdrs = {
        headers: {
          "X-Org-Id":
            (letter as any).organizationId ??
            (letter as any).organization_id ??
            "",
          "X-Proj-Id":
            (letter as any).projectId ?? (letter as any).project_id ?? "",
        },
      };
      await api.put(
        `/letters/${(letter as any).id ?? (letter as any)._id}`,
        {
          content,
          ...(refPayload ? { reference: refPayload } : {}),
        },
        hdrs
      );

      // Transition to Review
      await api.post(
        `/letters/${(letter as any).id ?? (letter as any)._id}/submit`,
        {},
        hdrs
      );
      const now = new Date().toISOString();
      onSave({
        ...(letter as any),
        content,
        reference: reference
          ? {
              id: reference.id,
              title: reference.title,
              subject: reference.subject,
              date: reference.date,
              referenceNumber: reference.referenceNumber,
            }
          : undefined,
        status: "Review" as any,
        updatedAt: now,
      } as any);
      toast.success("Submitted for review");
    } catch (e: any) {
      console.error("Submit for review failed", e);
      const status = e?.response?.status;
      const detail =
        e?.response?.data?.detail ||
        e?.response?.data?.message ||
        e?.message ||
        "Unknown error";
      toast.error("Failed to submit for review", {
        description: status ? `${status}: ${String(detail)}` : String(detail),
      });
    }
  };

  const handleSaveDraft = () => {
    const now = new Date().toISOString();
    onSave({
      ...(letter as any),
      content,
      reference: reference
        ? {
            id: reference.id,
            title: reference.title,
            subject: reference.subject,
            date: reference.date,
            referenceNumber: reference.referenceNumber,
          }
        : undefined,
      updatedAt: now,
    } as any);
  };

  const selectLetterReference = (selected: MappedReferenceLetter) => {
    setReference({
      id: selected.id,
      title: selected.title,
      subject: selected.subject,
      date: selected.createdAt,
      referenceNumber: selected.referenceNumber,
    });
    toast.success("Reference selected");
    setIsReferenceDialogOpen(false);
  };

  const removeReference = () => {
    setReference(undefined);
    toast.success("Reference removed");
  };

  // Extract key points from draft content (simple heuristic on bullets/lines)
  const extractedKeyPoints = useMemo(() => {
    const html = (content || "").trim();
    if (!html) return [];

    const htmlToPlainLines = (markup: string): string[] => {
      const div = document.createElement("div");
      div.innerHTML = markup;

      const lines: string[] = [];
      let buffer = "";
      const blockTags = new Set([
        "P",
        "DIV",
        "LI",
        "H1",
        "H2",
        "H3",
        "H4",
        "H5",
        "H6",
      ]);

      const walk = (node: Node) => {
        if (node.nodeType === Node.TEXT_NODE) {
          buffer += node.textContent || "";
        } else if (node.nodeType === Node.ELEMENT_NODE) {
          const el = node as HTMLElement;
          if (el.tagName === "BR") {
            lines.push(buffer);
            buffer = "";
          }
          el.childNodes.forEach(walk);
          if (blockTags.has(el.tagName)) {
            lines.push(buffer);
            buffer = "";
          }
        }
      };

      div.childNodes.forEach(walk);
      if (buffer.trim()) lines.push(buffer);

      return lines.map((l) => l.replace(/\s+/g, " ").trim()).filter(Boolean);
    };

    const lines = htmlToPlainLines(html)
      .map((l) =>
        l
          .replace(/^\s*[-*•]\s*/g, "")
          .replace(/^\s*\d+[.)]\s*/g, "")
          .trim()
      )
      .filter((l) => l.length > 0 && l.length <= 400);

    // Deduplicate while preserving order
    const seen = new Set<string>();
    const uniq: string[] = [];
    for (const l of lines) {
      if (!seen.has(l)) {
        seen.add(l);
        uniq.push(l);
      }
    }
    return uniq.slice(0, 15);
  }, [content]);

  const openSeekDialogForLine = (line: string) => {
    setPreselectedLine(line);
    setIsSeekDialogOpen(true);
  };

  // Local adapter to create an input request (used when opening from draft editor)
  const onInputRequestFromEditor = async (
    letterId: string,
    requestDetails: string,
    requestedUserId: string,
    dueDate?: Date,
    keyPoints?: string,
    referenceLetterId?: string
  ) => {
    try {
      await api.post(
        `/input-requests/letter/${letterId}`,
        {
          requested_from: requestedUserId,
          details: requestDetails,
          key_points: keyPoints,
          due_date: dueDate ? dueDate.toISOString() : undefined,
          reference_letter_id: referenceLetterId,
        },
        {
          headers: {
            "X-Org-Id":
              (letter as any).organizationId ??
              (letter as any).organization_id ??
              "",
            "X-Proj-Id":
              (letter as any).projectId ?? (letter as any).project_id ?? "",
          },
        }
      );
      toast.success("Input request sent");
      setIsSeekDialogOpen(false);
    } catch (e: any) {
      toast.error("Failed to send input request", {
        description: e?.response?.data?.detail || e?.message || "Unknown error",
      });
      throw e;
    }
  };

  return (
    <div className="space-y-4">
      <div className="rounded-md border p-4">
        <div className="grid grid-cols-2 gap-4 mb-4">
          <div>
            <Label className="text-muted-foreground text-sm">
              Letter Title
            </Label>
            <div className="font-medium">{(letter as any).title}</div>
          </div>
          <div>
            <Label className="text-muted-foreground text-sm">Recipient</Label>
            <div className="font-medium">{(letter as any).recipient}</div>
          </div>
        </div>

        <div className="mb-2">
          <Label className="text-muted-foreground text-sm">Subject</Label>
          <div className="font-medium">{(letter as any).subject}</div>
        </div>
        <div className="mb-4">
          <Label className="text-muted-foreground text-sm">Letter Number</Label>
          <div className="font-medium">{letterNumber}</div>
        </div>

        {/* Reference Section */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-2">
            <Label className="text-muted-foreground text-sm">Reference</Label>
            <Dialog
              open={isReferenceDialogOpen}
              onOpenChange={setIsReferenceDialogOpen}
            >
              <DialogTrigger asChild>
                <Button variant="outline" size="sm" className="gap-1">
                  <Plus className="h-4 w-4" />
                  Add Reference
                </Button>
              </DialogTrigger>
              <DialogContent className="max-w-3xl">
                <DialogHeader>
                  <DialogTitle>Select a Letter Reference</DialogTitle>
                </DialogHeader>

                <div className="relative mb-4">
                  <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
                  <Input
                    placeholder="Search by title, subject, reference number, or recipient..."
                    className="pl-9"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                  />
                </div>

                <div className="min-h-[80px]">
                  {isLoadingReferences ? (
                    <div className="flex items-center gap-2 text-sm text-muted-foreground">
                      <Loader2 className="h-4 w-4 animate-spin" /> Loading...
                    </div>
                  ) : referenceError ? (
                    <div className="text-sm text-red-600">{referenceError}</div>
                  ) : (
                    <div className="overflow-y-auto max-h-[300px]">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead>Reference #</TableHead>
                            <TableHead>Title</TableHead>
                            <TableHead>Date</TableHead>
                            <TableHead>Recipient</TableHead>
                            <TableHead>Action</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {filteredLetters.length > 0 ? (
                            filteredLetters.map((prevLetter) => (
                              <TableRow key={prevLetter.id}>
                                <TableCell className="font-medium">
                                  {prevLetter.referenceNumber}
                                </TableCell>
                                <TableCell>{prevLetter.title}</TableCell>
                                <TableCell>
                                  {format(
                                    new Date(prevLetter.createdAt),
                                    "MMM d, yyyy"
                                  )}
                                </TableCell>
                                <TableCell>{prevLetter.recipient}</TableCell>
                                <TableCell>
                                  <Button
                                    size="sm"
                                    variant="outline"
                                    onClick={() =>
                                      selectLetterReference(prevLetter)
                                    }
                                  >
                                    Select
                                  </Button>
                                </TableCell>
                              </TableRow>
                            ))
                          ) : (
                            <TableRow>
                              <TableCell
                                colSpan={5}
                                className="text-center py-4 text-muted-foreground"
                              >
                                No letters found matching your search
                              </TableCell>
                            </TableRow>
                          )}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </div>

                <DialogFooter>
                  <Button
                    variant="outline"
                    onClick={() => setIsReferenceDialogOpen(false)}
                  >
                    Cancel
                  </Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
          </div>

          {reference ? (
            <div className="p-3 bg-muted rounded-md border relative">
              <Button
                variant="ghost"
                size="icon"
                className="absolute top-2 right-2 h-6 w-6"
                onClick={removeReference}
              >
                <X className="h-4 w-4" />
              </Button>
              <div className="grid grid-cols-2 gap-2 text-sm">
                <div>
                  <span className="text-muted-foreground">
                    Reference Number:
                  </span>
                  <div className="font-medium">{reference.referenceNumber}</div>
                </div>
                <div>
                  <span className="text-muted-foreground">Date:</span>
                  <div className="font-medium">
                    {format(new Date(reference.date), "MMM d, yyyy")}
                  </div>
                </div>
                <div className="col-span-2">
                  <span className="text-muted-foreground">Title:</span>
                  <div className="font-medium">{reference.title}</div>
                </div>
                <div className="col-span-2">
                  <span className="text-muted-foreground">Subject:</span>
                  <div className="font-medium">{reference.subject}</div>
                </div>
              </div>
            </div>
          ) : (
            <div className="text-sm text-muted-foreground italic">
              No reference selected
            </div>
          )}
        </div>

        {/* Key Points section derived from draft content */}
        <div className="mb-6">
          <div className="flex items-center justify-between mb-2">
            <Label className="text-muted-foreground text-sm">Key Points</Label>
            <div className="text-xs text-muted-foreground">
              Click a line to request input for that item
            </div>
          </div>
          {extractedKeyPoints.length === 0 ? (
            <div className="text-sm text-muted-foreground italic">
              No key points detected yet. Start writing your draft content
              below.
            </div>
          ) : (
            <ul className="space-y-1">
              {extractedKeyPoints.map((kp, idx) => (
                <li key={idx}>
                  <button
                    type="button"
                    className="w-full text-left text-sm px-2 py-1 rounded hover:bg-accent hover:text-accent-foreground transition flex items-center gap-2"
                    onClick={() => openSeekDialogForLine(kp)}
                    title="Click to request input for this line"
                  >
                    <MessageCircleQuestion className="h-4 w-4 text-muted-foreground" />
                    <span className="whitespace-pre-wrap">{kp}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>

        <Tabs defaultValue="editor" className="w-full">
          <TabsList className="grid w-full grid-cols-3">
            <TabsTrigger value="editor">Letter Editor</TabsTrigger>
            <TabsTrigger value="ai-assistant" className="gap-2">
              <Sparkles className="h-4 w-4" />
              AI Assistant
            </TabsTrigger>
            <TabsTrigger value="deep-planning" className="gap-2">
              <FileSearch className="h-4 w-4" />
              Deep Planning
            </TabsTrigger>
          </TabsList>

          <TabsContent value="editor" className="mt-4">
            <div className="mb-4">
              <Label className="block mb-2">Letter Content</Label>
              <ReactQuill
                theme="snow"
                value={content}
                onChange={setContent}
                modules={quillModules}
                formats={quillFormats}
                placeholder="Compose your letter here..."
              />
              <div className="text-xs text-muted-foreground mt-1">
                Rich text editor enabled. Content will be saved as HTML.
              </div>
            </div>
          </TabsContent>

          <TabsContent value="ai-assistant" className="mt-4">
            <AIAssistant
              letterContent={content}
              onContentSuggestion={setContent}
              letterContext={{
                title: (letter as any).title,
                recipient: (letter as any).recipient,
                subject: (letter as any).subject,
                inputInfo: ((letter as any).inputRequests || []).find(
                  (req: any) => req?.response
                )?.response,
              }}
            />
          </TabsContent>

          <TabsContent value="deep-planning" className="mt-4">
            <DeepPlanningAssistant
              letterContent={content}
              onContentSuggestion={setContent}
              letterContext={{
                title: (letter as any).title,
                recipient: (letter as any).recipient,
                subject: (letter as any).subject,
                inputInfo: ((letter as any).inputRequests || []).find(
                  (req: any) => req?.response
                )?.response,
              }}
              documentId={
                (letter as any).documentId || (letter as any).document_id
              }
            />
          </TabsContent>
        </Tabs>
      </div>

      <div className="flex justify-end gap-2">
        <Button variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button variant="outline" onClick={handleSaveDraft}>
          Save Draft
        </Button>
        <Button onClick={handleSubmitForReview}>Submit for Review</Button>
      </div>

      {/* Seek Input Dialog opened from key point click */}
      <Dialog open={isSeekDialogOpen} onOpenChange={setIsSeekDialogOpen}>
        <DialogContent className="sm:max-w-4xl w-[95vw] max-w-[1200px]">
          <DialogHeader>
            <DialogTitle>Request Information or Clarification</DialogTitle>
          </DialogHeader>
          <RequestInputForm
            users={users}
            letterId={(letter as any).id ?? (letter as any)._id ?? ""}
            onRequestSent={() => setIsSeekDialogOpen(false)}
            onCancel={() => setIsSeekDialogOpen(false)}
            preselectedLineItem={preselectedLine}
            onInputRequest={onInputRequestFromEditor}
          />
        </DialogContent>
      </Dialog>
    </div>
  );
}
