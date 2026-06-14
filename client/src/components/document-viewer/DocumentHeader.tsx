import React from "react";
import { useParams, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type { BadgeProps as UIBadgeProps } from "@/components/ui/badge";
import {
  ArrowLeft,
  Download,
  Share,
  Edit,
  Tag,
  LinkIcon,
  Mail,
  Loader2,
  X,
} from "lucide-react";
import { Link } from "react-router-dom";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Checkbox } from "@/components/ui/checkbox";
import { useForm } from "react-hook-form";
import { toast } from "sonner";
import type { LocalDocument } from "@/pages/DocumentViewerPage";
import { emailService, EmailSuggestion } from "@/services/email-service";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";

interface DocumentHeaderProps {
  document: LocalDocument | null;
  showMetadata: boolean;
  setShowMetadata: (show: boolean) => void;
  isShareDialogOpen: boolean;
  setIsShareDialogOpen: (open: boolean) => void;
  isLinkReferenceDialogOpen: boolean;
  setIsLinkReferenceDialogOpen: (open: boolean) => void;
  relatedDocuments: Array<{
    id: string;
    name: string;
    date: string;
    type: string;
  }>;
}

type ShareFormValues = {
  subject: string;
  message: string;
  includeLinkedDocs: boolean;
  includeLetterLink: boolean;
  includeRefs: boolean;
};

const DocumentHeader: React.FC<DocumentHeaderProps> = ({
  document,
  showMetadata,
  setShowMetadata,
  isShareDialogOpen,
  setIsShareDialogOpen,
  setIsLinkReferenceDialogOpen,
  relatedDocuments,
}) => {
  const [isSending, setIsSending] = React.useState(false);
  const navigate = useNavigate();
  const { id } = useParams();

  // Suggestions are managed per RecipientEditor instance (field-scoped).

  // Recipient source selector (persist in localStorage)
  const [recipientSource, setRecipientSource] = React.useState<
    "representatives" | "parties" | "both"
  >((localStorage.getItem("share_recipient_source") as any) || "both");

  // To/CC/BCC recipients and input text
  const [toRecipients, setToRecipients] = React.useState<string[]>([]);
  const [ccRecipients, setCcRecipients] = React.useState<string[]>([]);
  const [bccRecipients, setBccRecipients] = React.useState<string[]>([]);
  // Deprecated: field focus tracked locally in RecipientEditor
  const [toInput, setToInput] = React.useState("");
  const [ccInput, setCcInput] = React.useState("");
  const [bccInput, setBccInput] = React.useState("");

  const statusBadgeVariant: UIBadgeProps["variant"] = React.useMemo(() => {
    if (document?.status === "Approved") return "success";
    if (document?.status === "Draft") return "neutral";
    if (document?.status === "Rejected") return "danger";
    return "outline";
  }, [document?.status]);

  const recipientsCount = React.useMemo(() => {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    const all = new Set([...toRecipients, ...ccRecipients, ...bccRecipients]);

    // Include any valid pending input values so the counter reflects what users see typed
    const pending = [toInput, ccInput, bccInput]
      .map((v) => v.trim().toLowerCase())
      .filter((v) => emailRegex.test(v));
    pending.forEach((v) => all.add(v));

    return all.size;
  }, [toRecipients, ccRecipients, bccRecipients, toInput, ccInput, bccInput]);

  // References picker
  type RefOption = { id: string; label: string };
  const [refOptions, setRefOptions] = React.useState<RefOption[]>([]);
  const [selectedRefIds, setSelectedRefIds] = React.useState<string[]>([]);

  const shareForm = useForm<ShareFormValues>({
    defaultValues: {
      subject: `Sharing document: ${document?.filename || ""}`,
      message: `Hello,\n\nI'm sharing the document "${
        document?.filename || ""
      }" with you.\n\nPlease review and let me know if you have any questions.\n\nRegards,\nDocument Owner`,
      includeLinkedDocs: false,
      includeLetterLink: true,
      includeRefs: false,
    },
  });

  // Suggestions are loaded per field; removed global effect.

  // Field-scoped suggestion loader moved into RecipientEditor.

  const handleShareSubmit = async (values: ShareFormValues) => {
    if (!document?._id) {
      toast.error("Document ID not available");
      return;
    }

    try {
      setIsSending(true);

      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

      // Merge pending text input with committed chips so users don't lose the last typed address
      const uniqueTo = Array.from(
        new Set(
          [...toRecipients, toInput]
            .map((e) => (e || "").trim().toLowerCase())
            .filter((e) => e && emailRegex.test(e))
        )
      );
      const uniqueCc = Array.from(
        new Set(
          [...ccRecipients, ccInput]
            .map((e) => (e || "").trim().toLowerCase())
            .filter((e) => e && emailRegex.test(e))
        )
      );
      const uniqueBcc = Array.from(
        new Set(
          [...bccRecipients, bccInput]
            .map((e) => (e || "").trim().toLowerCase())
            .filter((e) => e && emailRegex.test(e))
        )
      );

      if (uniqueTo.length === 0) {
        toast.error("Add at least one recipient in To");
        return;
      }

      // Validate emails
      const all = [...uniqueTo, ...uniqueCc, ...uniqueBcc];
      const invalid = all.filter((e) => !emailRegex.test(e));
      if (invalid.length) {
        toast.error(`Invalid email(s): ${invalid.join(", ")}`);
        return;
      }

      const shareRequest = {
        to: uniqueTo,
        cc: uniqueCc,
        bcc: uniqueBcc,
        subject: values.subject,
        message: values.message,
        document_id: document._id!,
        include_linked_documents: values.includeLinkedDocs,
        include_letter_link: values.includeLetterLink,
        reference_ids: values.includeRefs ? selectedRefIds : [],
      };

      const response = await emailService.shareDocument(shareRequest);

      toast.success("Document shared successfully", {
        description: `Recipients: ${uniqueTo.length}${
          uniqueCc.length ? ` (+CC ${uniqueCc.length})` : ""
        }. ${response.attachments_count} file(s) attached.`,
      });

      setIsShareDialogOpen(false);
      shareForm.reset();
      setToRecipients([]);
      setCcRecipients([]);
      setBccRecipients([]);
      setToInput("");
      setCcInput("");
      setBccInput("");
      setSelectedRefIds([]);
    } catch (error: any) {
      console.error("Error sharing document:", error);
      toast.error("Failed to share document", {
        description: error.message || "Please try again later",
      });
    } finally {
      setIsSending(false);
    }
  };

  const handleDownload = async () => {
    try {
      if (!id) {
        toast.error("Document ID not available.");
        return;
      }

      // Always prefer the authenticated backend endpoint; it streams local files
      // and redirects to presigned URLs when needed.
      const downloadApi = joinApiUrl(`/documents/${id}/download`);
      const response = await authenticatedFetch(downloadApi);

      if (!response.ok) {
        throw new Error(
          `Failed to download document: ${response.status} ${response.statusText}`
        );
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);

      const link = window.document.createElement("a");
      link.href = url;
      link.download = document?.filename || "document";
      window.document.body.appendChild(link);
      link.click();
      window.document.body.removeChild(link);
      window.URL.revokeObjectURL(url);

      toast.success("Document downloaded successfully");
    } catch (error: any) {
      console.error("Error downloading document:", error);
      toast.error("Failed to download document", {
        description: error?.message || "Please try again later.",
      });
    }
  };

  // Utilities to add/remove emails with cross-list dedupe
  const addEmail = React.useCallback(
    (bucket: "to" | "cc" | "bcc", email: string) => {
      const val = email.trim().toLowerCase();
      if (!val) return;

      setToRecipients((prev) =>
        bucket === "to"
          ? Array.from(new Set([...prev, val]))
          : prev.filter((x) => x !== val)
      );
      setCcRecipients((prev) =>
        bucket === "cc"
          ? Array.from(new Set([...prev, val]))
          : prev.filter((x) => x !== val)
      );
      setBccRecipients((prev) =>
        bucket === "bcc"
          ? Array.from(new Set([...prev, val]))
          : prev.filter((x) => x !== val)
      );
    },
    []
  );

  const RecipientEditor: React.FC<{
    label: string;
    field: "to" | "cc" | "bcc";
    values: string[];
    inputValue: string;
    setInputValue: (v: string) => void;
  }> = ({ label, field, values, inputValue, setInputValue }) => {
    const [open, setOpen] = React.useState(false);
    const [loading, setLoading] = React.useState(false);
    const [suggestions, setSuggestions] = React.useState<EmailSuggestion[]>([]);

    const loadSuggestions = React.useCallback(
      async (query?: string) => {
        try {
          setLoading(true);
          const res = await emailService.resolveRecipients({
            query,
            include_representatives: recipientSource !== "parties",
            include_parties: recipientSource !== "representatives",
            organization_id: (document?.organization_id as any) || undefined,
            project_id: (document?.project_id as any) || undefined,
          });
          setSuggestions(res);
        } catch {
          setSuggestions([]);
        } finally {
          setLoading(false);
        }
      },
      // RecipientEditor is declared inside DocumentHeader, so the hook linter
      // cannot classify parent-scope values correctly here.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [document, recipientSource]
    );

    return (
      <div>
        <FormLabel>
          {label}
          {values.length > 0 ? (
            <span className="ml-2 text-xs text-muted-foreground">
              ({values.length})
            </span>
          ) : null}
        </FormLabel>
        <div className="mt-1 relative">
          <div className="flex flex-wrap gap-2 p-2 border rounded-md">
            {values.map((e) => (
              <span
                key={`${field}-${e}`}
                className="inline-flex items-center gap-1 bg-muted px-2 py-1 rounded text-sm"
              >
                {e}
                <button
                  type="button"
                  onClick={() => {
                    if (field === "to") {
                      setToRecipients((prev) => prev.filter((x) => x !== e));
                    } else if (field === "cc") {
                      setCcRecipients((prev) => prev.filter((x) => x !== e));
                    } else {
                      setBccRecipients((prev) => prev.filter((x) => x !== e));
                    }
                  }}
                  className="text-muted-foreground hover:text-foreground"
                  aria-label={`Remove ${e}`}
                >
                  <X className="w-3 h-3" />
                </button>
              </span>
            ))}
            <input
              className="flex-1 min-w-[200px] outline-none"
              placeholder="Type to search or paste emails (Enter or , to add)"
              value={inputValue}
              onChange={(ev) => {
                const v = ev.target.value;
                setInputValue(v);
                setOpen(true);
                loadSuggestions(v);
              }}
              onFocus={() => setOpen(true)}
              onBlur={() => {
                const val = inputValue.trim().replace(/,$/, "");
                if (val) {
                  addEmail(field, val);
                  setInputValue("");
                }
                setOpen(false);
              }}
              onPaste={(ev) => {
                const text = ev.clipboardData.getData("text");
                const parts = text
                  .split(/[\s,;]+/)
                  .map((s) => s.trim())
                  .filter(Boolean);
                if (parts.length > 1) {
                  ev.preventDefault();
                }
                parts.forEach((p) => addEmail(field, p));
                setInputValue("");
                setOpen(false);
              }}
              onKeyDown={(ev) => {
                if (ev.key === "Enter" || ev.key === ",") {
                  ev.preventDefault();
                  const val = inputValue.trim().replace(/,$/, "");
                  if (val) {
                    addEmail(field, val);
                    setInputValue("");
                    setOpen(false);
                  }
                }
              }}
            />
          </div>
          {open && (inputValue.length > 0 || loading) && (
            <div className="absolute z-50 mt-1 w-full bg-white border rounded-md shadow">
              <div className="max-h-56 overflow-auto">
                {loading && (
                  <div className="p-2 text-sm text-muted-foreground">
                    Loading...
                  </div>
                )}
                {!loading && suggestions.length === 0 && (
                  <div className="p-2 text-sm text-muted-foreground">
                    No suggestions found.
                  </div>
                )}
                {suggestions.map((s) => (
                  <button
                    type="button"
                    key={`${field}-sugg-${s.email}`}
                    className="w-full text-left px-3 py-2 hover:bg-muted/50"
                    onMouseDown={(e) => e.preventDefault()}
                    onClick={() => {
                      addEmail(field, s.email);
                      setInputValue("");
                      setOpen(false);
                    }}
                  >
                    <div className="font-medium">{s.name}</div>
                    <div className="text-sm text-muted-foreground">
                      {s.email}
                    </div>
                    {s.organization && (
                      <div className="text-xs text-muted-foreground">
                        {s.organization}
                      </div>
                    )}
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    );
  };

  return (
    <div className="bg-white border-b px-4 py-2 flex items-center justify-between">
      <div className="flex items-center space-x-4">
        <Button variant="ghost" size="icon" asChild>
          <Link to="/documents">
            <ArrowLeft className="h-5 w-5" />
          </Link>
        </Button>
        <div>
          {document ? (
            <>
              <h1 className="text-lg font-medium">{document.filename}</h1>
              <div className="flex items-center space-x-2">
                <Badge variant="outline" className="text-xs">
                  {document.uploadType}
                </Badge>
                <Badge variant={statusBadgeVariant} className="text-xs">
                  {document.status}
                </Badge>
                <span className="text-xs text-muted-foreground">
                  {document.pages} pages • {document.size}
                </span>
              </div>
            </>
          ) : (
            <span className="text-lg font-medium">Loading document...</span>
          )}
        </div>
      </div>

      <div className="flex items-center space-x-2">
        <Button
          variant="ghost"
          size="sm"
          className="flex items-center gap-1"
          onClick={handleDownload}
        >
          <Download className="h-4 w-4" />
          <span className="hidden sm:inline">Download</span>
        </Button>

        <Button
          variant="ghost"
          size="sm"
          className="flex items-center gap-1"
          asChild
        >
          <Link to={`/share/${document?._id || ""}`}>
            <Share className="h-4 w-4" />
            <span className="hidden sm:inline">Share Document</span>
          </Link>
        </Button>

        <Button
          variant="ghost"
          size="sm"
          className="flex items-center gap-1"
          onClick={() => navigate(`/documents/summary/${document?._id}`)}
        >
          <LinkIcon className="h-6 w-6" />
          <span className="hidden sm:inline">Letter Summary</span>
        </Button>

        <Button variant="ghost" size="sm" className="flex items-center gap-1">
          <Edit className="h-6 w-6" />
          <span className="hidden sm:inline">Edit</span>
        </Button>
        <Button
          size="sm"
          className="flex items-center gap-1"
          onClick={() => setShowMetadata(!showMetadata)}
        >
          <Tag className="h-6 w-6" />
          <span className="hidden sm:inline">Metadata</span>
        </Button>
      </div>
    </div>
  );
};

export default DocumentHeader;
