import React, { useState, useEffect } from "react";
import { useLetterWorkflow } from "@/hooks/useLetterWorkflow";
import { LetterWorkflowHeader } from "@/components/letter-workflow/LetterWorkflowHeader";
import { LetterWorkflowTabs } from "@/components/letter-workflow/LetterWorkflowTabs";
import { LettersTable } from "@/components/letter-workflow/LettersTable";
import { LetterDialog } from "@/components/letter-workflow/LetterDialog";
import { useAIAssistant } from "@/hooks/useAIAssistant";
import { toast } from "sonner";

import { useSearchParams, useNavigate } from "react-router-dom";
import enhancedApi from "@/services/enhanced-api";

const LetterWorkflowPage = () => {
  const navigate = useNavigate();
  const {
    activeTab,
    setActiveTab,
    isInitiateDialogOpen,
    setIsInitiateDialogOpen,
    isRequestInputDialogOpen,
    setIsRequestInputDialogOpen,
    selectedLetter,
    setSelectedLetter,
    users,
    organizations,
    projects,
    letters,
    isLoading,
    error,
    handleLetterInitiation,
    handleLetterUpdate,
    handleInputRequest,
    formatDate,
    getFilteredLetters,
  } = useLetterWorkflow();

  const {
    searchSimilarLetters,
    generateEnhancedDraft,
    extractMetadata,
    isLoading: aiLoading,
    error: aiError,
  } = useAIAssistant();

  const [similarLetters, setSimilarLetters] = useState([]);
  const [aiGeneratedDraft, setAiGeneratedDraft] = useState("");
  const [extractedMetadata, setExtractedMetadata] = useState(null);
  const [initiatingDocId, setInitiatingDocId] = useState<string | null>(null);
  const [initiatingDoc, setInitiatingDoc] = useState<any | null>(null);

  // If navigated from Documents with ?requestDraftForDocumentId=..., auto-open initiation dialog
  const [searchParams, setSearchParams] = useSearchParams();
  useEffect(() => {
    const reqId = searchParams.get("requestDraftForDocumentId");
    if (reqId) {
      setIsInitiateDialogOpen(true);
      setInitiatingDocId(reqId);
      // Clear the param to avoid re-opening on rerenders
      searchParams.delete("requestDraftForDocumentId");
      setSearchParams(searchParams);
    }
  }, [searchParams, setSearchParams, setIsInitiateDialogOpen]);

  // Load initiating document details (org/project/letterNo) when coming from Documents
  useEffect(() => {
    if (!initiatingDocId) return;
    (async () => {
      try {
        const d = await enhancedApi.getDocument(initiatingDocId);
        setInitiatingDoc(d as any);
        // Persist org/project context if not already set so downstream API calls are scoped
        try {
          if ((d as any)?.organization_id && !localStorage.getItem("org_id")) {
            localStorage.setItem("org_id", String((d as any).organization_id));
          }
          if ((d as any)?.project_id && !localStorage.getItem("proj_id")) {
            localStorage.setItem("proj_id", String((d as any).project_id));
          }
        } catch {
          // non-fatal
        }
      } catch (err) {
        console.error("Error fetching initiating document:", err);
      }
    })();
  }, [initiatingDocId]);

  // If navigated with ?letterId=..., load that specific letter and select it
  useEffect(() => {
    const lid = searchParams.get("letterId");
    if (!lid) return;
    (async () => {
      try {
        const letter = await enhancedApi.getLetter(lid);
        if (letter) {
          setSelectedLetter(letter as any);
        } else {
          console.error("Letter not found");
        }
      } catch (err) {
        console.error("Error fetching letter by id:", err);
      }
    })();
    // keep param to allow refresh preserving selection
  }, [searchParams]);

  useEffect(() => {
    if (selectedLetter && selectedLetter.subject) {
      const fetchSimilarLetters = async () => {
        try {
          const result = await searchSimilarLetters(selectedLetter.subject);
          if (result && result.similar_letters) {
            setSimilarLetters(result.similar_letters);
          } else {
            console.error("No similar letters found");
          }
        } catch (err) {
          console.error("Error fetching similar letters:", err);
        }
      };
      fetchSimilarLetters();
    }
  }, [selectedLetter, searchSimilarLetters]);

  const handleEnhancedLetterInitiation = async (letterData) => {
    try {
      // Normalize form fields -> API payload
      const payload: any = { ...letterData };

      // Assigned drafter: accept assigned_to, assignedUserId, or assignedTo.id
      payload.assigned_to =
        letterData.assigned_to ||
        letterData.assignedUserId ||
        letterData.assignedTo?.id ||
        "";

      // Map organization/project from form names to API names.
      // Prefer explicit form values; otherwise use initiatingDoc context; then fall back to first available.
      payload.organization_id =
        letterData.organization_id ||
        letterData.organizationId ||
        (initiatingDoc as any)?.organization_id ||
        (organizations[0]?.id ?? "");
      payload.project_id =
        letterData.project_id ||
        letterData.projectId ||
        (initiatingDoc as any)?.project_id ||
        (projects[0]?.id ?? "");

      // If coming from a document with a letterNo, propagate it so the workflow shows the proper letter number
      if (!payload.letter_no && (initiatingDoc as any)?.letterNo) {
        payload.letter_no = (initiatingDoc as any).letterNo;
      }

      if (!payload.assigned_to) {
        console.error("assigned_to is required for letter initiation");
        return;
      }

      if (letterData.useAI) {
        const draft = await generateEnhancedDraft({
          subject: payload.subject,
          recipient: payload.recipient,
          user_id: payload.assigned_to,
          context: payload.context,
          metadata: extractedMetadata,
        });
        payload.content = draft.draft_letter;
        payload.aiGenerated = true;
        payload.similarLettersUsed = draft.similar_letters;
        setAiGeneratedDraft(draft.draft_letter);
      }

      // Remove UI-only fields
      delete payload.assignedUserId;
      delete payload.assignedTo;
      delete payload.organizationId;
      delete payload.projectId;

      // If initiated from a document, first reserve the draft (server-side guard) to prevent duplicates
      if (initiatingDocId) {
        try {
          await enhancedApi.requestDraftForDocument(initiatingDocId);
        } catch (e: any) {
          // 409 or any server-side guard message -> surface and abort initiation
          const message =
            e?.response?.data?.detail ||
            e?.message ||
            "Draft already in progress for this document";
          toast.error("Draft already in progress for this document", {
            description: message,
          });
          return;
        }
      }

      const created = await handleLetterInitiation(payload);
    } catch (err) {
      console.error("Error in enhanced letter initiation:", err);
    }
  };

  const handleMetadataExtraction = async (file) => {
    try {
      const metadata = await extractMetadata(file);
      setExtractedMetadata(metadata);
      toast.success("Metadata extracted successfully", {
        description: "The document details have been pulled in.",
      });
      return metadata;
    } catch (err) {
      console.error("Error extracting metadata:", err);
    }
  };

  // ---- UI adapters/mappers to satisfy component prop types ----
  const uiActiveTab =
    typeof activeTab === "string"
      ? activeTab.toLowerCase()
      : String(activeTab).toLowerCase();

  const setUiActiveTab = (tab: string) => {
    const normalized =
      tab === "all" ? "All" : tab.charAt(0).toUpperCase() + tab.slice(1);
    // Cast because hook uses union type "All" | LetterStatus
    setActiveTab(normalized as any);
  };

  const findUiUser = (id?: string) => {
    const u: any = users.find((x: any) => x.id === id);
    return {
      id: u?.id ?? id ?? "",
      name: u?.name ?? "User",
      email: u?.email ?? "",
      avatar: u?.avatar,
    };
  };

  const mapHookToUi = (h: any) => ({
    id: h.id,
    title: h.title,
    recipient: h.recipient,
    subject: h.subject,
    content: h.content ?? "",
    status: h.status,
    createdBy: findUiUser(h.created_by ?? h.createdBy ?? h.created_by_id),
    assignedTo: findUiUser(h.assigned_to ?? h.assignedTo?.id),
    organizationId: h.organization_id ?? h.organizationId,
    projectId: h.project_id ?? h.projectId,
    createdAt: h.created_at ?? h.createdAt ?? new Date().toISOString(),
    updatedAt: h.updated_at ?? h.updatedAt ?? new Date().toISOString(),
    statusStartDate: h.statusStartDate,
    comments: h.comments ?? [],
    inputRequests: h.inputRequests ?? [],
    // Preserve letter number so downstream UI can display it
    letter_no: h.letter_no,
    reference: h.reference
      ? {
          id: h.reference.id,
          title: h.reference.title,
          subject: h.reference.subject,
          date: h.reference.date,
          referenceNumber:
            h.reference.reference_number ?? h.reference.referenceNumber,
        }
      : undefined,
  });

  const mapUiToHook = (u: any) => ({
    id: u.id,
    title: u.title,
    recipient: u.recipient,
    subject: u.subject,
    content: u.content,
    status: u.status,
    created_by: u.createdBy?.id ?? "",
    assigned_to: u.assignedTo?.id ?? "",
    organization_id: u.organizationId,
    project_id: u.projectId,
    created_at: u.createdAt,
    updated_at: u.updatedAt,
    comments: u.comments,
    // Keep letter number on round-trips
    letter_no: u.letter_no,
    reference: u.reference
      ? {
          id: u.reference.id,
          title: u.reference.title,
          subject: u.reference.subject,
          date: u.reference.date,
          reference_number: u.reference.referenceNumber,
        }
      : undefined,
  });

  const uiLetters = (getFilteredLetters() as any[]).map(mapHookToUi);

  const onManageLetterBridge = (uiLetter: any) => {
    // Navigate to dedicated page instead of opening a modal
    const id = uiLetter?.id ?? uiLetter?._id;
    if (id) {
      navigate(`/letters/${id}`);
      return;
    }
    // Fallback: keep old behavior if id missing
    const hookLetter = mapUiToHook(uiLetter);
    setSelectedLetter(hookLetter as any);
  };

  // Row selection → set selected letter so "Request Input" targets the selected row
  const onSelectUiLetter = (uiLetter: any) => {
    const hookLetter = mapUiToHook(uiLetter);
    setSelectedLetter(hookLetter as any);
  };

  const onInputRequestBridge = async (
    letterId: string,
    requestDetails: string,
    requestedUserId: string,
    dueDate?: Date,
    keyPoints?: string,
    referenceLetterId?: string
  ) => {
    await handleInputRequest(letterId, {
      requested_from: requestedUserId,
      details: requestDetails,
      due_date: dueDate ? dueDate.toISOString() : undefined,
      key_points: keyPoints,
      reference_letter_id: referenceLetterId,
    });
  };

  const selectedUiLetter = selectedLetter
    ? mapHookToUi(selectedLetter as any)
    : null;

  const onLetterUpdateBridge = async (updatedUiLetter: any) => {
    const id = updatedUiLetter.id;
    const patch: any = {
      title: updatedUiLetter.title,
      recipient: updatedUiLetter.recipient,
      subject: updatedUiLetter.subject,
      content: updatedUiLetter.content,
      status: updatedUiLetter.status,
    };

    // Include reference if present (convert UI shape -> backend shape)
    if (updatedUiLetter.reference) {
      patch.reference = {
        id: updatedUiLetter.reference.id,
        title: updatedUiLetter.reference.title,
        subject: updatedUiLetter.reference.subject,
        date: updatedUiLetter.reference.date,
        reference_number:
          updatedUiLetter.reference.referenceNumber ??
          updatedUiLetter.reference.reference_number,
      };
    }

    const updated = await handleLetterUpdate(id, patch);

    // If the draft has been completed/finalized, set originating document to "Replied"
    if (initiatingDocId) {
      const finalStatuses = new Set([
        "Replied",
        "Completed",
        "Finalized",
        "Approved",
      ]);
      const nextStatus = updated?.status ?? updatedUiLetter?.status;
      if (nextStatus && finalStatuses.has(String(nextStatus))) {
        try {
          await enhancedApi.completeDraftForDocument(initiatingDocId);
        } catch (e) {
          console.error("Failed to set document status to Replied:", e);
        }
      }
    }

    setSelectedLetter(updated as any);
  };

  return (
    <div className="w-[98vw] max-w-[1440px] mx-auto px-6">
      <LetterWorkflowHeader
        isInitiateDialogOpen={isInitiateDialogOpen}
        setIsInitiateDialogOpen={setIsInitiateDialogOpen}
        isRequestInputDialogOpen={isRequestInputDialogOpen}
        setIsRequestInputDialogOpen={setIsRequestInputDialogOpen}
        onLetterInitiation={handleEnhancedLetterInitiation}
        onInputRequest={onInputRequestBridge}
        users={users}
        organizations={organizations}
        projects={projects}
        selectedLetterId={selectedLetter?.id}
        documentId={initiatingDocId || undefined}
      />

      <LetterWorkflowTabs activeTab={uiActiveTab} setActiveTab={setUiActiveTab}>
        <LettersTable
          letters={uiLetters as any}
          formatDate={formatDate}
          onManageLetter={onManageLetterBridge}
          onSelectLetter={onSelectUiLetter}
        />
      </LetterWorkflowTabs>

      <LetterDialog
        selectedLetter={selectedUiLetter as any}
        onOpenChange={(open) => !open && setSelectedLetter(null)}
        onLetterUpdate={onLetterUpdateBridge}
      />
    </div>
  );
};

export default LetterWorkflowPage;
