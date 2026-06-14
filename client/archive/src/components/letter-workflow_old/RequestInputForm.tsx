import React, { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Calendar } from "@/components/ui/calendar";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { CalendarIcon, MessageCircleQuestion, UserCheck } from "lucide-react";
import { format } from "date-fns";
import { toast } from "sonner";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { api } from "@/services/api";

interface User {
  id: string;
  name: string;
  email: string;
  avatar?: string;
}

interface RequestInputFormProps {
  users: User[];
  letterId: string;
  onRequestSent: () => void;
  onCancel: () => void;
  // Optional: initiating document context to auto-populate key points/summary
  documentId?: string;
  // Optional: when opening the dialog from a draft key point, preselect that line item
  preselectedLineItem?: string;
  onInputRequest: (
    letterId: string,
    requestDetails: string,
    requestedUserId: string,
    dueDate?: Date,
    keyPoints?: string,
    referenceLetterId?: string
  ) => void;
}

const RequestInputForm: React.FC<RequestInputFormProps> = ({
  users,
  letterId,
  onRequestSent,
  onCancel,
  documentId,
  preselectedLineItem,
  onInputRequest,
}) => {
  const [selectedUserId, setSelectedUserId] = useState<string>("");
  const [requestDetails, setRequestDetails] = useState<string>("");
  const [dueDate, setDueDate] = useState<Date | undefined>(undefined);
  const [keyPoints, setKeyPoints] = useState<string>("");
  const [loadingKeypoints, setLoadingKeypoints] = useState(false);
  const [keypointsError, setKeypointsError] = useState<string | null>(null);
  const [lineItems, setLineItems] = useState<
    { text: string; seek: "No" | "Yes" }[]
  >([]);
  // Reference letter support
  const [referenceLetters, setReferenceLetters] = useState<
    { id: string; title: string; subject: string; referenceNumber?: string }[]
  >([]);
  const [selectedRefLetterId, setSelectedRefLetterId] = useState<string>("");
  const [refSummary, setRefSummary] = useState<string>("");

  useEffect(() => {
    const loadKeyPoints = async () => {
      // Prefer document metadata if documentId provided; fallback to letter-based suggestion
      setLoadingKeypoints(true);
      setKeypointsError(null);
      try {
        if (documentId) {
          try {
            const { data } = await api.get(`/documents/${documentId}`);
            // Build aggregated key points from available metadata
            const lines: string[] = [];
            const addText = (val?: string) => {
              if (!val || typeof val !== "string") return;
              val
                .split(/\r?\n/)
                .map((s) => s.trim())
                .filter((s) => s.length > 0)
                .forEach((s) => lines.push(s));
            };
            const addList = (arr?: any[], prefix?: string) => {
              if (!Array.isArray(arr)) return;
              arr
                .map((x) => (typeof x === "string" ? x : JSON.stringify(x)))
                .map((s) => s.trim())
                .filter((s) => s.length > 0)
                .forEach((s) => lines.push(prefix ? `${prefix}: ${s}` : s));
            };
            // Known fields from backend model
            addText((data && data.summary) as string | undefined);
            // Some legacy payloads may expose key_points on document
            addText((data && (data as any).key_points) as string | undefined);
            if (data && data.subject)
              lines.push(`Subject: ${String(data.subject)}`);
            // Tags, SubTags (names or ids)
            if (
              Array.isArray((data as any).tags) &&
              (data as any).tags.length
            ) {
              const tagNames = (data as any).tags
                .map((t: any) => String(t))
                .join(", ");
              lines.push(`Tags: ${tagNames}`);
            }
            if (
              Array.isArray((data as any).subTags) &&
              (data as any).subTags.length
            ) {
              const subTagNames = (data as any).subTags
                .map((t: any) => String(t))
                .join(", ");
              lines.push(`SubTags: ${subTagNames}`);
            }
            // Keywords, contractual clauses
            addList((data as any)?.keywords, "Keyword");
            const clauses = (data as any)?.contractual_clauses;
            if (Array.isArray(clauses)) {
              clauses.forEach((c: any) => {
                const text =
                  typeof c === "string" ? c : c?.text ?? JSON.stringify(c);
                if (text && String(text).trim().length > 0) {
                  lines.push(`Clause: ${String(text).trim()}`);
                }
              });
            }
            // Dedupe while preserving order
            const seen = new Set<string>();
            const uniq = lines.filter((l) => {
              const key = l.toLowerCase();
              if (seen.has(key)) return false;
              seen.add(key);
              return true;
            });
            const combined = uniq.join("\n");
            if (combined.trim().length > 0) {
              setKeyPoints(combined);
              return;
            }
          } catch {
            // fallback to letter-based suggestion
          }
        }
        if (!letterId) return;
        const { data } = await api.get(
          `/input-requests/letter/${letterId}/suggested-key-points`
        );
        if (data && typeof data.key_points === "string") {
          setKeyPoints(data.key_points);
        }
      } catch (e: any) {
        setKeypointsError(
          e?.response?.data?.detail ||
            e?.message ||
            "Failed to load key points from document"
        );
      } finally {
        setLoadingKeypoints(false);
      }
    };
    const loadLetters = async () => {
      try {
        // Fetch recent letters scoped by backend auth (org/project)
        const { data } = await api.get<any[]>("/letters", {
          params: { limit: 50 },
        });
        const mapped =
          (Array.isArray(data) ? data : []).map((l: any) => ({
            id: String(l.id ?? l._id ?? ""),
            title: l.title ?? "",
            subject: l.subject ?? "",
            referenceNumber:
              l?.reference?.reference_number ?? l?.reference_number,
          })) ?? [];
        setReferenceLetters(mapped);
      } catch (e) {
        // optional
      }
    };
    loadKeyPoints();
    loadLetters();
  }, [letterId, documentId]);

  // Derive selectable line items from key points text
  useEffect(() => {
    const lines = (keyPoints || "")
      .split("\n")
      .map((l) =>
        l
          .replace(/^\s*[-*•]\s*/g, "")
          .replace(/^\s*\d+[.)]\s*/g, "")
          .trim()
      )
      .filter((l) => l.length > 0);
    setLineItems(
      lines.map((t) => ({
        text: t,
        seek: preselectedLineItem && t === preselectedLineItem ? "Yes" : "No",
      }))
    );
  }, [keyPoints, preselectedLineItem]);

  // Load reference summary when a reference letter is selected
  useEffect(() => {
    const loadRefSummary = async () => {
      if (!selectedRefLetterId) {
        setRefSummary("");
        return;
      }
      try {
        const { data } = await api.get(
          `/input-requests/letter/${selectedRefLetterId}/suggested-key-points`
        );
        setRefSummary(
          data && typeof data.key_points === "string" ? data.key_points : ""
        );
      } catch (e) {
        setRefSummary("");
      }
    };
    loadRefSummary();
  }, [selectedRefLetterId]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();

    if (!selectedUserId) {
      toast.error("Please select a user to request input from");
      return;
    }

    if (!requestDetails.trim()) {
      toast.error("Please provide details about what input you need");
      return;
    }

    const selectedLines = lineItems.filter((li) => li.seek === "Yes");

    // If any line items are selected, submit one input request per selected line
    if (selectedLines.length > 0) {
      for (const li of selectedLines) {
        const detailsForLine = `Line item: ${li.text}\n\n${requestDetails}`;
        await onInputRequest(
          letterId,
          detailsForLine,
          selectedUserId,
          dueDate,
          li.text,
          selectedRefLetterId || undefined
        );
      }
    } else {
      // Fallback: single input request with all key points
      await onInputRequest(
        letterId,
        requestDetails,
        selectedUserId,
        dueDate,
        keyPoints,
        selectedRefLetterId || undefined
      );
    }

    // Notify other components (e.g., Input tab) to refresh their request lists
    try {
      window.dispatchEvent(
        new CustomEvent("input-request:created", { detail: { letterId } })
      );
    } catch {
      // no-op
    }

    onRequestSent();
  };

  return (
    <form onSubmit={handleSubmit} className="space-y-4">
      <div>
        <Label htmlFor="recipient">Request Input From</Label>
        <Select value={selectedUserId} onValueChange={setSelectedUserId}>
          <SelectTrigger id="recipient" className="w-full">
            <SelectValue placeholder="Select user" />
          </SelectTrigger>
          <SelectContent>
            {users.map((user) => (
              <SelectItem key={user.id} value={user.id}>
                <div className="flex items-center gap-2">
                  <Avatar className="h-6 w-6">
                    <AvatarImage src={user.avatar} />
                    <AvatarFallback>{user.name.charAt(0)}</AvatarFallback>
                  </Avatar>
                  <span>{user.name}</span>
                </div>
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div>
        <Label>Reference Letter (Optional)</Label>
        <Select
          value={selectedRefLetterId}
          onValueChange={setSelectedRefLetterId}
        >
          <SelectTrigger className="w-full">
            <SelectValue placeholder="Select letter to reference (optional)" />
          </SelectTrigger>
          <SelectContent>
            {referenceLetters.map((rl) => (
              <SelectItem key={rl.id} value={rl.id}>
                {(rl.referenceNumber ? rl.referenceNumber + " — " : "") +
                  (rl.title || rl.subject)}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      <div>
        <Label htmlFor="key-points">Key Points (Auto)</Label>
        {loadingKeypoints && (
          <p className="text-xs text-muted-foreground mb-2">
            Loading key points...
          </p>
        )}
        {keypointsError && (
          <p className="text-xs text-red-600 mb-2">{keypointsError}</p>
        )}
        <Textarea
          id="key-points"
          value={keyPoints}
          onChange={(e) => setKeyPoints(e.target.value)}
          rows={5}
          placeholder="Auto-extracted summary of the letter will appear here. You can edit before sending."
          className="resize-none mb-3"
        />

        {selectedRefLetterId && (
          <div className="mb-3">
            <Label className="text-sm">Reference Letter Summary</Label>
            <Textarea
              readOnly
              value={refSummary}
              rows={4}
              className="resize-none mt-1"
              placeholder="No summary available for selected letter"
            />
          </div>
        )}

        {lineItems.length > 0 && (
          <div className="border rounded-md p-3 mb-3">
            <div className="text-sm font-medium mb-2">Line items</div>
            <div className="space-y-2">
              {lineItems.map((li, idx) => (
                <div key={idx} className="flex items-start gap-2">
                  <Select
                    value={li.seek}
                    onValueChange={(val) =>
                      setLineItems((prev) =>
                        prev.map((p, i) =>
                          i === idx ? { ...p, seek: val as "No" | "Yes" } : p
                        )
                      )
                    }
                  >
                    <SelectTrigger className="w-28">
                      <SelectValue placeholder="Seek Input" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="No">No</SelectItem>
                      <SelectItem value="Yes">Yes</SelectItem>
                    </SelectContent>
                  </Select>
                  <div className="text-sm flex-1 whitespace-pre-wrap">
                    {li.text}
                  </div>
                </div>
              ))}
            </div>
            <p className="text-xs text-muted-foreground mt-2">
              Choose “Yes” to request input for a specific line item.
            </p>
          </div>
        )}

        <Label htmlFor="request-details">What information do you need?</Label>
        <Textarea
          id="request-details"
          value={requestDetails}
          onChange={(e) => setRequestDetails(e.target.value)}
          rows={4}
          placeholder="Describe what information or clarification you need..."
          className="resize-none"
        />
      </div>

      <div>
        <Label htmlFor="due-date">Due Date (Optional)</Label>
        <Popover>
          <PopoverTrigger asChild>
            <Button
              variant="outline"
              className="w-full justify-start text-left font-normal"
              id="due-date"
            >
              <CalendarIcon className="mr-2 h-4 w-4" />
              {dueDate ? format(dueDate, "PPP") : <span>Select a date</span>}
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-auto p-0">
            <Calendar
              mode="single"
              selected={dueDate}
              onSelect={setDueDate}
              initialFocus
            />
          </PopoverContent>
        </Popover>
        <p className="text-sm text-muted-foreground mt-1">
          If you need the input by a specific date, please select it here
        </p>
      </div>

      <div className="flex justify-end gap-2 pt-2">
        <Button type="button" variant="outline" onClick={onCancel}>
          Cancel
        </Button>
        <Button type="submit" className="gap-2" disabled={loadingKeypoints}>
          <MessageCircleQuestion className="h-4 w-4" />
          Send Input Request
        </Button>
      </div>
    </form>
  );
};

export default RequestInputForm;
