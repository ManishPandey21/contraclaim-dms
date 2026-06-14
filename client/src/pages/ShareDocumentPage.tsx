import React from "react";
import { useParams, Link, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/button";
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
import { toast } from "sonner";
import { emailService, EmailSuggestion } from "@/services/email-service";
import { emailGroupsApi, EmailGroup } from "@/services/email-groups-api";
import enhancedApi, { Document as ApiDocument } from "@/services/enhanced-api";
import { joinApiUrl } from "@/config/api";
import { authenticatedFetch } from "@/services/http";
import {
  X,
  ArrowLeft,
  Loader2,
  Mail,
  AlertTriangle,
  RefreshCw,
} from "lucide-react";
import { Label } from "@/components/ui/label";
import { useForm } from "react-hook-form";

type ShareFormValues = {
  subject: string;
  message: string;
  includeLinkedDocs: boolean;
  shareViaLink: boolean;
  attachFileToEmail: boolean;
  includeRefs: boolean;
  messageFormat: "text" | "html";
  registeredBy: string;
  distributionFor: "answer" | "information";
};

type RefOption = { id: string; label: string };

const ShareDocumentPage = () => {
  const { id: documentId } = useParams<{ id: string }>();
  const navigate = useNavigate();

  const [document, setDocument] = React.useState<ApiDocument | null>(null);
  const [isLoading, setIsLoading] = React.useState(true);
  const [loadError, setLoadError] = React.useState<string | null>(null);
  const [retryCounter, setRetryCounter] = React.useState(0);
  const [isSending, setIsSending] = React.useState(false);

  // Recipient source selector (persist in localStorage)
  const [recipientSource, setRecipientSource] = React.useState<
    "representatives" | "parties" | "both"
  >((localStorage.getItem("share_recipient_source") as any) || "both");

  // To/CC/BCC recipients and input text
  const [toRecipients, setToRecipients] = React.useState<string[]>([]);
  const [ccRecipients, setCcRecipients] = React.useState<string[]>([]);
  const [bccRecipients, setBccRecipients] = React.useState<string[]>([]);
  const [toInput, setToInput] = React.useState("");
  const [ccInput, setCcInput] = React.useState("");
  const [bccInput, setBccInput] = React.useState("");

  // References
  const [refOptions, setRefOptions] = React.useState<RefOption[]>([]);
  const [selectedRefIds, setSelectedRefIds] = React.useState<string[]>([]);
  const [enclosureCount, setEnclosureCount] = React.useState<number>(0);
  // Groups
  const [groups, setGroups] = React.useState<EmailGroup[]>([]);
  const [selectedGroupIds, setSelectedGroupIds] = React.useState<string[]>([]);
  const [groupTarget, setGroupTarget] = React.useState<"to" | "cc" | "bcc">(
    "to"
  );
  const [showCc, setShowCc] = React.useState(false);
  const [showBcc, setShowBcc] = React.useState(false);

  const recipientsCount = React.useMemo(() => {
    const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    const all = new Set([...toRecipients, ...ccRecipients, ...bccRecipients]);
    const pending = [toInput, ccInput, bccInput]
      .map((v) => v.trim().toLowerCase())
      .filter((v) => emailRegex.test(v));
    pending.forEach((v) => all.add(v));
    return all.size;
  }, [toRecipients, ccRecipients, bccRecipients, toInput, ccInput, bccInput]);

  // Load document and reference/enclosure info
  React.useEffect(() => {
    const load = async () => {
      if (!documentId) {
        setDocument(null);
        setLoadError("Document ID is missing.");
        setIsLoading(false);
        return;
      }
      try {
        setLoadError(null);
        const doc = await enhancedApi.getDocument(documentId);
        setDocument(doc);
        // Preload references meta for picker when enabled
        try {
          const refsRes = await authenticatedFetch(
            joinApiUrl(`/documents/${documentId}/references`),
            {}
          );
          if (refsRes.ok) {
            const refs = await refsRes.json();
            const mapped = (refs || []).map((r: any) => ({
              id: r.documentId || r._id,
              label: `${r.letterNo || r.letter_no || ""} • ${(r.date || "")
                .toString()
                .slice(0, 10)}`,
            }));
            setRefOptions(mapped);
          }
        } catch {
          // ignore
        }
        try {
          const enclosures = await enhancedApi.getDocumentEnclosures(
            documentId
          );
          setEnclosureCount(enclosures?.length || 0);
        } catch {
          setEnclosureCount(0);
        }

        // Load email groups for this document's org/project scope
        try {
          const list = await emailGroupsApi.list({
            organization_id: (doc as any)?.organization_id || undefined,
            project_id: (doc as any)?.project_id || undefined,
          });
          setGroups(list);
        } catch {
          setGroups([]);
        }
      } catch (e) {
        console.error("Failed to load document", e);
        setDocument(null);
        setLoadError(e instanceof Error ? e.message : "Failed to load document");
        toast.error("Failed to load document");
      } finally {
        setIsLoading(false);
      }
    };
    load();
  }, [documentId, retryCounter]);

  const buildDefaultMessage = React.useCallback(
    (name?: string, docData?: any) => {
      return `Please review the shared document: ${
        name || "the requested document"
      }.`;
      const safeName = name || "the requested file";
      const letterNo = docData?.letterNo || docData?.letter_no || "";
      const date = docData?.date
        ? new Date(docData.date).toLocaleDateString()
        : "";
      const source = docData?.source || docData?.from || "";

      return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Document Distribution - ContraClaim DMS</title>
</head>
<body style="margin: 0; padding: 0; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; background-color: #f5f7fa;">
    <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #f5f7fa; padding: 20px 0;">
        <tr>
            <td align="center">
                <table width="600" cellpadding="0" cellspacing="0" border="0" style="background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); overflow: hidden;">
                    <!-- Header Section -->
                    <tr>
                        <td style="background: linear-gradient(135deg, #1e5bb8 0%, #2874d8 100%); padding: 30px 40px; text-align: left;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td>
                                        <div style="display: inline-block; background-color: #ffffff; width: 50px; height: 50px; border-radius: 8px; text-align: center; line-height: 50px; vertical-align: middle;">
                                            <span style="color: #1e5bb8; font-size: 24px; font-weight: bold;">CC</span>
                                        </div>
                                    </td>
                                    <td style="padding-left: 15px;">
                                        <h1 style="margin: 0; color: #ffffff; font-size: 28px; font-weight: 600;">ContraClaim DMS</h1>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Title Section -->
                    <tr>
                        <td style="padding: 30px 40px 20px 40px;">
                            <h2 style="margin: 0; color: #1e5bb8; font-size: 22px; font-weight: 600;">Sharing Document</h2>
                        </td>
                    </tr>
                    <!-- Notification Box -->
                    <tr>
                        <td style="padding: 0 40px 30px 40px;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="background-color: #e8f2ff; border-left: 4px solid #2874d8; border-radius: 4px;">
                                <tr>
                                    <td style="padding: 20px;">
                                        <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                            <tr>
                                                <td width="40" valign="top">
                                                    <div style="background-color: #2874d8; width: 35px; height: 35px; border-radius: 4px; text-align: center; line-height: 35px;">
                                                        <span style="color: #ffffff; font-size: 20px;">📄</span>
                                                    </div>
                                                </td>
                                                <td style="padding-left: 15px;">
                                                    <h3 style="margin: 0 0 5px 0; color: #1e5bb8; font-size: 16px; font-weight: 600;">NEW DOCUMENT DISTRIBUTED FOR INFORMATION</h3>
                                                    <p style="margin: 0; color: #4a5568; font-size: 14px; line-height: 1.5;">A new document has been shared with you via ContraClaim DMS</p>
                                                </td>
                                            </tr>
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Document Details -->
                    <tr>
                        <td style="padding: 0 40px 30px 40px;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0" style="border-left: 3px solid #2874d8; padding-left: 20px;">
                                <tr>
                                    <td>
                                        <p style="margin: 0 0 15px 0; color: #2d3748; font-size: 15px; font-weight: 600; line-height: 1.6;">
                                            ${safeName}
                                        </p>
                                        <p style="margin: 0 0 20px 0; color: #4a5568; font-size: 14px; line-height: 1.6;">
                                            This document requires your review. Please access it through the ContraClaim DMS portal.
                                        </p>
                                        ${
                                          letterNo
                                            ? `<div style="background-color: #f8fafc; padding: 15px; border-radius: 4px; margin-bottom: 20px;">
                                            <p style="margin: 0; color: #2874d8; font-size: 14px; font-weight: 500;">
                                                ${letterNo}
                                            </p>
                                        </div>`
                                            : ""
                                        }
                                        <!-- Document Metadata -->
                                        <table width="100%" cellpadding="0" cellspacing="0" border="0" style="margin-top: 15px;">
                                            ${
                                              date
                                                ? `<tr>
                                                <td style="padding: 8px 0;">
                                                    <span style="color: #1e5bb8; font-weight: 600; font-size: 14px;">Date:</span>
                                                    <span style="color: #4a5568; font-size: 14px; margin-left: 5px;">${date}</span>
                                                </td>
                                            </tr>`
                                                : ""
                                            }
                                            ${
                                              source
                                                ? `<tr>
                                                <td style="padding: 8px 0;">
                                                    <span style="color: #1e5bb8; font-weight: 600; font-size: 14px;">Source:</span>
                                                    <span style="color: #4a5568; font-size: 14px; margin-left: 5px;">${source}</span>
                                                </td>
                                            </tr>`
                                                : ""
                                            }
                                            <!-- SHARE_METADATA_PLACEHOLDER -->
                                        </table>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Action Button Placeholder -->
                    <tr>
                        <td style="padding: 0 40px 30px 40px;">
                            <table width="100%" cellpadding="0" cellspacing="0" border="0">
                                <tr>
                                    <td align="center">
                                        <p style="margin: 0; color: #718096; font-size: 14px;">
                                            Click the "View Document" button below to access the document.
                                        </p>
                                    </td>
                                </tr>
                            </table>
                        </td>
                    </tr>
                    <!-- Divider -->
                    <tr>
                        <td style="padding: 0 40px;">
                            <div style="border-bottom: 1px solid #e2e8f0;"></div>
                        </td>
                    </tr>
                    <!-- Footer -->
                    <tr>
                        <td style="padding: 25px 40px; text-align: center;">
                            <p style="margin: 0 0 10px 0; color: #718096; font-size: 13px; line-height: 1.6;">
                                This is an automated message from ContraClaim DMS.<br>
                                Please do not reply to this email.
                            </p>
                            <p style="margin: 0; color: #a0aec0; font-size: 12px;">
                                © 2025 ContraClaim. All rights reserved.
                            </p>
                        </td>
                    </tr>
                </table>
            </td>
        </tr>
    </table>
</body>
</html>`;
    },
    []
  );

  const shareForm = useForm<ShareFormValues>({
    defaultValues: {
      subject: "Sharing document",
      message: buildDefaultMessage(),
      includeLinkedDocs: false,
      shareViaLink: true,
      attachFileToEmail: false,
      includeRefs: false,
      messageFormat: "html",
      registeredBy: "",
      distributionFor: "information",
    },
  });
  const includeRefsEnabled = shareForm.watch("includeRefs");
  const shareViaLinkEnabled = shareForm.watch("shareViaLink");
  const attachFileEnabled = shareForm.watch("attachFileToEmail");
  const [formPrefilled, setFormPrefilled] = React.useState(false);

  React.useEffect(() => {
    setFormPrefilled(false);
  }, [documentId]);

  React.useEffect(() => {
    if (document && !formPrefilled) {
      // Prioritize subject (letter subject) over filename for better context
      const docLabel =
        document.subject ||
        document.letterNo ||
        document.filename ||
        "Document";
      const currentValues = shareForm.getValues();
      const docRegisteredBy =
        (document as any)?.registeredBy ||
        (document as any)?.registered_by ||
        currentValues.registeredBy ||
        "";
      const distributionValue = currentValues.distributionFor || "information";
      shareForm.reset({
        ...currentValues,
        subject: `Sharing document: ${docLabel}`,
        message: buildDefaultMessage(docLabel),
        messageFormat: "html",
        registeredBy: docRegisteredBy,
        distributionFor: distributionValue,
      });
      setFormPrefilled(true);
    }
  }, [document, buildDefaultMessage, formPrefilled, shareForm]);

  React.useEffect(() => {
    if (ccRecipients.length && !showCc) {
      setShowCc(true);
    }
  }, [ccRecipients, showCc]);

  React.useEffect(() => {
    if (bccRecipients.length && !showBcc) {
      setShowBcc(true);
    }
  }, [bccRecipients, showBcc]);

  React.useEffect(() => {
    if (!includeRefsEnabled || refOptions.length === 0) {
      return;
    }
    setSelectedRefIds((prev) => {
      if (!prev.length) {
        return refOptions.map((opt) => opt.id);
      }
      const existing = new Set(prev);
      let changed = false;
      refOptions.forEach((opt) => {
        if (!existing.has(opt.id)) {
          existing.add(opt.id);
          changed = true;
        }
      });
      return changed ? Array.from(existing) : prev;
    });
  }, [includeRefsEnabled, refOptions]);

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
  const bulkAddEmails = React.useCallback(
    (bucket: "to" | "cc" | "bcc", emails: string[]) => {
      emails.forEach((entry) => addEmail(bucket, entry));
    },
    [addEmail]
  );

  // Recipient editor (field-scoped suggestion list)
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
    const initialLoadRef = React.useRef(false);

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
      // RecipientEditor is declared inside ShareDocumentPage, so the hook
      // linter cannot classify parent-scope values correctly here.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      [document, recipientSource]
    );

    React.useEffect(() => {
      initialLoadRef.current = false;
      if (open) {
        loadSuggestions(inputValue);
        initialLoadRef.current = true;
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [inputValue, loadSuggestions, open, recipientSource]);

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
              onFocus={() => {
                setOpen(true);
                if (!initialLoadRef.current) {
                  loadSuggestions(inputValue);
                  initialLoadRef.current = true;
                }
              }}
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
          {open && (
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

  const shareFormInstance = shareForm;

  const handleShareSubmit = async (values: ShareFormValues) => {
    if (!document?._id) {
      toast.error("Document ID not available");
      return;
    }
    try {
      setIsSending(true);
      const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
      if (!values.shareViaLink && !values.attachFileToEmail) {
        toast.error("Choose Share via link, Attach file to email, or both");
        return;
      }

      const rawEmails = [
        ...toRecipients,
        toInput,
        ...ccRecipients,
        ccInput,
        ...bccRecipients,
        bccInput,
      ]
        .map((e) => (e || "").trim().toLowerCase())
        .filter(Boolean);
      const invalid = rawEmails.filter((e) => !emailRegex.test(e));
      if (invalid.length) {
        toast.error(`Invalid email(s): ${invalid.join(", ")}`);
        return;
      }

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

      const activeRefIds = values.includeRefs
        ? selectedRefIds.length
          ? selectedRefIds
          : refOptions.map((opt) => opt.id)
        : [];

      const request = {
        to: uniqueTo,
        cc: uniqueCc,
        bcc: uniqueBcc,
        subject: values.subject,
        message: values.message,
        document_id: document._id!,
        include_linked_documents: values.includeLinkedDocs,
        share_via_link: values.shareViaLink,
        attach_file_to_email: values.attachFileToEmail,
        include_refs: values.includeRefs,
        reference_ids: activeRefIds,
        email_format: values.messageFormat,
        registered_by: values.registeredBy?.trim() || undefined,
        distribution_for: values.distributionFor,
      };

      const response = await emailService.shareDocument(request);
      toast.success("Document shared successfully", {
        description: `Recipients: ${uniqueTo.length}${
          uniqueCc.length ? ` (+CC ${uniqueCc.length})` : ""
        }. ${
          response.delivery_methods?.share_via_link ? "Link shared" : "No link"
        }; ${response.attachments_count} file(s) attached. Returning to document...`,
      });

      // Reset and navigate back
      shareFormInstance.reset();
      setToRecipients([]);
      setCcRecipients([]);
      setBccRecipients([]);
      setToInput("");
      setCcInput("");
      setBccInput("");
      setSelectedRefIds([]);

      navigate(`/documentviewer/${document._id}`);
    } catch (error: any) {
      console.error("Error sharing document:", error);
      toast.error("Failed to share document", {
        description: error?.message || "Please try again later",
      });
    } finally {
      setIsSending(false);
    }
  };

  if (isLoading) {
    return (
      <div className="p-6">
        <div className="max-w-5xl mx-auto space-y-4">
          <div className="h-9 w-48 rounded bg-muted animate-pulse" />
          <div className="rounded-lg border bg-white p-6">
            <div className="h-5 w-64 rounded bg-muted animate-pulse" />
            <div className="mt-6 grid gap-4 md:grid-cols-2">
              <div className="h-10 rounded bg-muted animate-pulse" />
              <div className="h-10 rounded bg-muted animate-pulse" />
              <div className="h-28 rounded bg-muted animate-pulse md:col-span-2" />
            </div>
          </div>
        </div>
      </div>
    );
  }

  if (!document) {
    return (
      <div className="p-6">
        <div className="max-w-5xl mx-auto rounded-lg border bg-white p-8 text-center">
          <AlertTriangle className="mx-auto mb-3 h-8 w-8 text-red-600" />
          <h1 className="text-lg font-semibold">Document not available</h1>
          <p className="mx-auto mt-2 max-w-md text-sm text-muted-foreground">
            {loadError || "The document could not be found or you do not have access."}
          </p>
          <div className="mt-6 flex justify-center gap-3">
            <Button variant="outline" onClick={() => navigate(-1)}>
              Back
            </Button>
            <Button onClick={() => setRetryCounter((value) => value + 1)}>
              <RefreshCw className="mr-2 h-4 w-4" />
              Retry
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6">
      <div className="max-w-5xl mx-auto space-y-6">
        <div className="flex items-center gap-3">
          <Button variant="ghost" size="icon" asChild>
            <Link to={`/documentviewer/${document._id}`}>
              <ArrowLeft className="h-5 w-5" />
            </Link>
          </Button>
          <div>
            <h1 className="text-xl font-semibold">Share Document</h1>
            <p className="text-sm text-muted-foreground">{document.filename}</p>
          </div>
        </div>

        <div className="rounded-lg border bg-white">
          <div className="p-6">
            <Form {...shareFormInstance}>
              <form
                onSubmit={shareFormInstance.handleSubmit(handleShareSubmit)}
                className="space-y-5"
              >
                {/* Recipient source selector */}
                <div className="space-y-2">
                  <FormLabel>Recipient Source</FormLabel>
                  <div className="flex items-center gap-6">
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="radio"
                        name="recipient_source"
                        value="representatives"
                        checked={recipientSource === "representatives"}
                        onChange={() => {
                          setRecipientSource("representatives");
                          localStorage.setItem(
                            "share_recipient_source",
                            "representatives"
                          );
                        }}
                      />
                      Representatives
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="radio"
                        name="recipient_source"
                        value="parties"
                        checked={recipientSource === "parties"}
                        onChange={() => {
                          setRecipientSource("parties");
                          localStorage.setItem(
                            "share_recipient_source",
                            "parties"
                          );
                        }}
                      />
                      Parties
                    </label>
                    <label className="flex items-center gap-2 text-sm">
                      <input
                        type="radio"
                        name="recipient_source"
                        value="both"
                        checked={recipientSource === "both"}
                        onChange={() => {
                          setRecipientSource("both");
                          localStorage.setItem(
                            "share_recipient_source",
                            "both"
                          );
                        }}
                      />
                      Both
                    </label>
                  </div>
                </div>

                {/* Recipients */}
                <div className="space-y-3">
                  {/* Group selection */}
                  {groups.length > 0 && (
                    <div className="space-y-2">
                      <Label>Groups</Label>
                      <div className="border rounded-md p-2 max-h-40 overflow-auto">
                        {groups.map((g) => (
                          <label
                            key={g._id}
                            className="flex items-center gap-2 text-sm py-1"
                          >
                            <input
                              type="checkbox"
                              checked={selectedGroupIds.includes(g._id)}
                              onChange={(e) => {
                                const checked = e.target.checked;
                                setSelectedGroupIds((prev) =>
                                  checked
                                    ? Array.from(new Set([...prev, g._id]))
                                    : prev.filter((id) => id !== g._id)
                                );
                              }}
                            />
                            <span className="font-medium">{g.name}</span>
                            <span className="text-xs text-muted-foreground">
                              {(g.category || "").toString()} •{" "}
                              {g.emails?.length || 0} member(s)
                            </span>
                          </label>
                        ))}
                      </div>
                      <div className="flex flex-wrap items-center gap-3">
                        <div className="flex items-center gap-2 text-xs text-muted-foreground">
                          <span>Add selected groups to:</span>
                          <select
                            className="rounded border px-2 py-1 text-xs"
                            value={groupTarget}
                            onChange={(e) =>
                              setGroupTarget(
                                e.target.value as "to" | "cc" | "bcc"
                              )
                            }
                          >
                            <option value="to">To</option>
                            <option value="cc">CC</option>
                            <option value="bcc">BCC</option>
                          </select>
                        </div>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => {
                            const selected = groups.filter((g) =>
                              selectedGroupIds.includes(g._id)
                            );
                            const emails = Array.from(
                              new Set(selected.flatMap((g) => g.emails || []))
                            );
                            if (!emails.length) {
                              toast.message("Select at least one group", {
                                description:
                                  "Choose a group before attempting to add recipients.",
                              });
                              return;
                            }
                            bulkAddEmails(groupTarget, emails);
                            toast.success(
                              `Added ${emails.length} recipient${
                                emails.length > 1 ? "s" : ""
                              } to ${groupTarget.toUpperCase()}`
                            );
                          }}
                        >
                          Apply Group Recipients
                        </Button>
                      </div>
                    </div>
                  )}
                  <RecipientEditor
                    label="To"
                    field="to"
                    values={toRecipients}
                    inputValue={toInput}
                    setInputValue={setToInput}
                  />
                  <div className="flex flex-wrap items-center gap-4 text-sm">
                    {!showCc && (
                      <button
                        type="button"
                        className="text-primary hover:underline"
                        onClick={() => setShowCc(true)}
                      >
                        + Add CC{" "}
                        {ccRecipients.length ? `(${ccRecipients.length})` : ""}
                      </button>
                    )}
                    {!showBcc && (
                      <button
                        type="button"
                        className="text-primary hover:underline"
                        onClick={() => setShowBcc(true)}
                      >
                        + Add BCC{" "}
                        {bccRecipients.length
                          ? `(${bccRecipients.length})`
                          : ""}
                      </button>
                    )}
                  </div>
                  {!showCc && ccRecipients.length > 0 && (
                    <div className="text-xs text-muted-foreground pl-1">
                      CC hidden • {ccRecipients.length} recipient
                      {ccRecipients.length > 1 ? "s" : ""}
                    </div>
                  )}
                  {showCc && (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>CC Recipients</span>
                        <button
                          type="button"
                          className="text-primary hover:underline"
                          onClick={() => setShowCc(false)}
                        >
                          Hide CC
                        </button>
                      </div>
                      <RecipientEditor
                        label="CC"
                        field="cc"
                        values={ccRecipients}
                        inputValue={ccInput}
                        setInputValue={setCcInput}
                      />
                    </div>
                  )}
                  {!showBcc && bccRecipients.length > 0 && (
                    <div className="text-xs text-muted-foreground pl-1">
                      BCC hidden • {bccRecipients.length} recipient
                      {bccRecipients.length > 1 ? "s" : ""}
                    </div>
                  )}
                  {showBcc && (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-xs text-muted-foreground">
                        <span>BCC Recipients</span>
                        <button
                          type="button"
                          className="text-primary hover:underline"
                          onClick={() => setShowBcc(false)}
                        >
                          Hide BCC
                        </button>
                      </div>
                      <RecipientEditor
                        label="BCC"
                        field="bcc"
                        values={bccRecipients}
                        inputValue={bccInput}
                        setInputValue={setBccInput}
                      />
                    </div>
                  )}
                </div>

                <div className="text-sm text-muted-foreground">
                  Recipients selected: {recipientsCount}
                </div>

                {/* Delivery options */}
                <div className="space-y-3">
                  <div>
                    <FormLabel>Delivery options</FormLabel>
                    <p className="text-sm text-muted-foreground">
                      Select one or both delivery methods for this share.
                    </p>
                  </div>
                  <div className="grid gap-3 md:grid-cols-2">
                    <FormField
                      control={shareFormInstance.control}
                      name="shareViaLink"
                      render={({ field }) => (
                        <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md border p-4">
                          <FormControl>
                            <Checkbox
                              checked={field.value}
                              onCheckedChange={field.onChange}
                            />
                          </FormControl>
                          <div className="space-y-1 leading-none">
                            <FormLabel>Share via link</FormLabel>
                            <p className="text-sm text-muted-foreground">
                              Email a secure public download link that does not
                              require recipient login.
                            </p>
                          </div>
                        </FormItem>
                      )}
                    />
                    <FormField
                      control={shareFormInstance.control}
                      name="attachFileToEmail"
                      render={({ field }) => (
                        <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md border p-4">
                          <FormControl>
                            <Checkbox
                              checked={field.value}
                              onCheckedChange={field.onChange}
                            />
                          </FormControl>
                          <div className="space-y-1 leading-none">
                            <FormLabel>Attach file to email</FormLabel>
                            <p className="text-sm text-muted-foreground">
                              Attach the primary document. Large files may need
                              the link option instead.
                            </p>
                          </div>
                        </FormItem>
                      )}
                    />
                  </div>
                  {!shareViaLinkEnabled && !attachFileEnabled && (
                    <p className="text-sm text-destructive">
                      Choose at least one delivery option.
                    </p>
                  )}
                </div>

                {/* Include refs */}
                <FormField
                  control={shareFormInstance.control}
                  name="includeRefs"
                  render={({ field }) => (
                    <FormItem className="flex flex-col gap-2 rounded-md border p-4">
                      <div className="flex flex-row items-start space-x-3">
                        <FormControl>
                          <Checkbox
                            checked={field.value}
                            onCheckedChange={(checked) => {
                              const nextValue = !!checked;
                              field.onChange(nextValue);
                              if (nextValue) {
                                setSelectedRefIds((prev) =>
                                  prev.length
                                    ? prev
                                    : refOptions.map((opt) => opt.id)
                                );
                              } else {
                                setSelectedRefIds([]);
                              }
                            }}
                          />
                        </FormControl>
                        <div className="space-y-1 leading-none">
                          <FormLabel>Include reference letters</FormLabel>
                          <p className="text-sm text-muted-foreground">
                            Select reference letters to include their share
                            links.
                          </p>
                        </div>
                      </div>

                      {field.value && (
                        <div className="mt-2 max-h-40 overflow-auto space-y-2">
                          {refOptions.length === 0 ? (
                            <div className="text-sm text-muted-foreground">
                              No references found.
                            </div>
                          ) : (
                            refOptions.map((opt) => {
                              const checked = selectedRefIds.includes(opt.id);
                              return (
                                <label
                                  key={opt.id}
                                  className="flex items-center gap-2 text-sm"
                                >
                                  <input
                                    type="checkbox"
                                    checked={checked}
                                    onChange={(e) => {
                                      const isOn = e.target.checked;
                                      setSelectedRefIds((prev) =>
                                        isOn
                                          ? Array.from(
                                              new Set([...prev, opt.id])
                                            )
                                          : prev.filter((x) => x !== opt.id)
                                      );
                                    }}
                                  />
                                  {opt.label}
                                </label>
                              );
                            })
                          )}
                        </div>
                      )}
                    </FormItem>
                  )}
                />

                {/* Message format */}
                <FormField
                  control={shareFormInstance.control}
                  name="messageFormat"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Message format</FormLabel>
                      <FormControl>
                        <div className="flex items-center gap-6">
                          <label className="flex items-center gap-2 text-sm">
                            <input
                              type="radio"
                              name="message_format"
                              value="text"
                              checked={field.value === "text"}
                              onChange={() => field.onChange("text")}
                            />
                            Plain text
                          </label>
                          <label className="flex items-center gap-2 text-sm">
                            <input
                              type="radio"
                              name="message_format"
                              value="html"
                              checked={field.value === "html"}
                              onChange={() => field.onChange("html")}
                            />
                            HTML (formatted)
                          </label>
                        </div>
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {/* Subject */}
                <FormField
                  control={shareFormInstance.control}
                  name="subject"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Subject</FormLabel>
                      <FormControl>
                        <Input
                          placeholder="Email subject"
                          {...field}
                          required
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={shareFormInstance.control}
                  name="registeredBy"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Registered by</FormLabel>
                      <FormControl>
                        <Input
                          placeholder='Name of the registering user (e.g., "John Doe")'
                          {...field}
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                <FormField
                  control={shareFormInstance.control}
                  name="distributionFor"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Distributed for</FormLabel>
                      <FormControl>
                        <div className="flex items-center gap-6">
                          <label className="flex items-center gap-2 text-sm">
                            <input
                              type="radio"
                              name="distribution_for"
                              value="information"
                              checked={field.value === "information"}
                              onChange={() => field.onChange("information")}
                            />
                            Information
                          </label>
                          <label className="flex items-center gap-2 text-sm">
                            <input
                              type="radio"
                              name="distribution_for"
                              value="answer"
                              checked={field.value === "answer"}
                              onChange={() => field.onChange("answer")}
                            />
                            Answer
                          </label>
                        </div>
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {/* Message */}
                <FormField
                  control={shareFormInstance.control}
                  name="message"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel>Message</FormLabel>
                      <p className="text-xs text-muted-foreground">
                        Supports plain text or basic HTML based on the selected
                        format.
                      </p>
                      <FormControl>
                        <Textarea
                          placeholder="Enter your message"
                          {...field}
                          rows={8}
                          required
                        />
                      </FormControl>
                      <FormMessage />
                    </FormItem>
                  )}
                />

                {/* Link enclosures */}
                <FormField
                  control={shareFormInstance.control}
                  name="includeLinkedDocs"
                  render={({ field }) => (
                    <FormItem className="flex flex-row items-start space-x-3 space-y-0 rounded-md border p-4">
                      <FormControl>
                        <Checkbox
                          checked={field.value}
                          onCheckedChange={field.onChange}
                        />
                      </FormControl>
                      <div className="space-y-1 leading-none">
                        <FormLabel>Link enclosures</FormLabel>
                        <p className="text-sm text-muted-foreground">
                          Include links to all enclosures in the email (
                          {enclosureCount} documents)
                        </p>
                      </div>
                    </FormItem>
                  )}
                />

                <div className="flex items-center justify-between pt-2">
                  <Button variant="outline" type="button" asChild>
                    <Link to={`/documentviewer/${document._id}`}>Cancel</Link>
                  </Button>
                  <Button type="submit" disabled={isSending}>
                    {isSending ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        Sending...
                      </>
                    ) : (
                      <>
                        <Mail className="mr-2 h-4 w-4" />
                        Send Email
                      </>
                    )}
                  </Button>
                </div>
              </form>
            </Form>
          </div>
        </div>
      </div>
    </div>
  );
};

export default ShareDocumentPage;
