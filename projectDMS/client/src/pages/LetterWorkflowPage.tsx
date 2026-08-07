import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useLetterWorkflow } from '@/hooks/useLetterWorkflow';
import { LetterWorkflowHeader } from '@/components/letter-workflow/LetterWorkflowHeader';
import { LetterWorkflowTabs } from '@/components/letter-workflow/LetterWorkflowTabs';
import { LettersTable } from '@/components/letter-workflow/LettersTable';
import { Skeleton } from '@/components/ui/skeleton';
import {
  mapLettersToUi,
  UILetter,
} from '@/utils/letterWorkflowMapping';
import enhancedApi from '@/services/enhanced-api';
import { LETTER_INITIATION_PREFILL_KEY } from '@/constants/storageKeys';
import { useTenant } from '@/contexts/TenantContext';

const normalizeName = (value?: string | null) =>
  typeof value === 'string' ? value.toLowerCase().trim() : undefined;

interface LetterInitiationPrefill {
  document_id?: string;
  letter_no?: string;
  subject?: string;
  recipient?: string;
  organization_id?: string;
  organization_name?: string;
  project_id?: string;
  project_name?: string;
  draft_reserved?: boolean;
}

const LetterWorkflowPage = () => {
  const { selectedOrganizationId, selectedProjectId, selectOrganization, selectProject } = useTenant();
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
    currentUser,
    organizations,
    projects,
    letters,
    isLoading,
    error,
    handleLetterInitiation,
    handleInputRequest,
    assignContractDrafter,
    assignReviewer,
    formatDate,
    fetchLetters,
  } = useLetterWorkflow();

  const [searchParams, setSearchParams] = useSearchParams();

  const [initiatingDocId, setInitiatingDocId] = useState<string | null>(null);
  const [documentPrefill, setDocumentPrefill] =
    useState<LetterInitiationPrefill | null>(null);
  const [prefillReserved, setPrefillReserved] = useState(false);
  const [selectedLetterId, setSelectedLetterId] = useState<string | undefined>(
    selectedLetter?.id
  );

  useEffect(() => {
    if (selectedLetter?.id) {
      setSelectedLetterId(selectedLetter.id);
    }
  }, [selectedLetter]);

  useEffect(() => {
    const docId = searchParams.get('requestDraftForDocumentId');
    if (docId) {
      setIsInitiateDialogOpen(true);
      setInitiatingDocId(docId);
      setDocumentPrefill(null);
      const next = new URLSearchParams(searchParams);
      next.delete('requestDraftForDocumentId');
      setSearchParams(next, { replace: true });
    }
  }, [searchParams, setSearchParams, setIsInitiateDialogOpen]);

  useEffect(() => {
    if (!initiatingDocId) {
      setDocumentPrefill(null);
      setPrefillReserved(false);
      return;
    }

    let isActive = true;
    setDocumentPrefill(null);

    const loadPrefill = async () => {
      let prefill: LetterInitiationPrefill | null = null;

      if (typeof window !== 'undefined') {
        const raw = window.sessionStorage.getItem(
          LETTER_INITIATION_PREFILL_KEY
        );
        if (raw) {
          try {
            const parsed = JSON.parse(raw) as LetterInitiationPrefill;
            if (!parsed.document_id) {
              parsed.document_id = initiatingDocId;
            }
            if (
              !parsed.document_id ||
              parsed.document_id === initiatingDocId
            ) {
              prefill = parsed;
            }
          } catch (error) {
            console.warn(
              '[LetterWorkflowPage] Failed to parse draft prefill cache',
              error
            );
          } finally {
            window.sessionStorage.removeItem(
              LETTER_INITIATION_PREFILL_KEY
            );
          }
        }
      }

      if (!prefill) {
        try {
          const document = await enhancedApi.getDocument(initiatingDocId);
          const uploadType = String(
            (document as any)?.uploadType ?? ''
          ).toLowerCase();
          prefill = {
            document_id: initiatingDocId,
            letter_no:
              (document as any)?.letterNo ??
              (document as any)?.letter_no ??
              '',
            subject: (document as any)?.subject ?? '',
            recipient:
              uploadType === 'incoming'
                ? (document as any)?.from_ ??
                  (document as any)?.from ??
                  ''
                : (document as any)?.to ?? '',
            organization_id: (document as any)?.organization_id ?? '',
            organization_name:
              (document as any)?.organization_name ??
              (document as any)?.organization ??
              undefined,
            project_id: (document as any)?.project_id ?? '',
            project_name:
              (document as any)?.project_name ??
              (document as any)?.project ??
              undefined,
          };
        } catch (error) {
          console.error(
            '[LetterWorkflowPage] Failed to load document prefill',
            error
          );
        }
      }

      if (!isActive) {
        return;
      }

      setDocumentPrefill(prefill);
      setPrefillReserved(Boolean(prefill?.draft_reserved));

      if (prefill && typeof window !== 'undefined') {
        try {
          if (prefill.organization_id && !selectedOrganizationId) {
            selectOrganization(String(prefill.organization_id));
          }
          if (prefill.project_id && !selectedProjectId) {
            selectProject(String(prefill.project_id));
          }
        } catch {
          // Invalid or inaccessible deep-link scope is non-blocking.
        }
      }
    };

    loadPrefill();

    return () => {
      isActive = false;
    };
  }, [initiatingDocId, selectedOrganizationId, selectedProjectId, selectOrganization, selectProject]);

  const clearInitiationContext = useCallback(() => {
    setInitiatingDocId(null);
    setDocumentPrefill(null);
    setPrefillReserved(false);
    if (typeof window !== 'undefined') {
      window.sessionStorage.removeItem(LETTER_INITIATION_PREFILL_KEY);
    }
  }, []);

  const uiLetters: UILetter[] = useMemo(
    () => mapLettersToUi(letters, users),
    [letters, users]
  );

  const currentUserRoles = useMemo(() => {
    const rawRoles = currentUser?.roles;
    if (Array.isArray(rawRoles)) {
      return rawRoles.map((role) => String(role).toLowerCase());
    }
    if (typeof rawRoles === 'string') {
      return rawRoles.split(',').map((role) => role.trim().toLowerCase());
    }
    return [];
  }, [currentUser]);

  const canAssignDrafter = useMemo(() => {
    const managerRoles = new Set([
      'superadmin',
      'contraclaim_drafting_manager',
      'contract_manager',
      'contract manager',
      'headcontract',
      'contractmgr_org',
      'contractmgr_proj',
    ]);
    return currentUserRoles.some((role) => managerRoles.has(role));
  }, [currentUserRoles]);

  const isContractLetterDrafter = useMemo(() => {
    const drafterRoles = new Set([
      'contraclaim_expert_drafter',
      'contract_letter_drafter',
      'contract letter drafter',
      'letter_drafter',
      'letter drafter',
      'contract_drafter',
      'contract drafter',
    ]);
    return (
      currentUserRoles.some((role) => drafterRoles.has(role)) &&
      !canAssignDrafter
    );
  }, [canAssignDrafter, currentUserRoles]);

  const visibleLetters = useMemo(() => {
    if (!isContractLetterDrafter || !currentUser?.id) {
      return uiLetters;
    }
    return uiLetters.filter(
      (letter) => String(letter.assignedTo?.id ?? '') === String(currentUser.id)
    );
  }, [currentUser?.id, isContractLetterDrafter, uiLetters]);

  const filteredLetters = useMemo(() => {
    if (activeTab === 'All') return visibleLetters;
    return visibleLetters.filter((letter) => letter.status === activeTab);
  }, [visibleLetters, activeTab]);

  const selectedUiLetter = useMemo(
    () => uiLetters.find((letter) => letter.id === selectedLetterId),
    [selectedLetterId, uiLetters]
  );

  const handleInitiation = useCallback(
    async (payload: Parameters<typeof handleLetterInitiation>[0]) => {
      const normalizedPayload = { ...payload };

      if (initiatingDocId && documentPrefill) {
        if (
          !normalizedPayload.organization_id &&
          documentPrefill.organization_id
        ) {
          normalizedPayload.organization_id = documentPrefill.organization_id;
        }
        if (
          !normalizedPayload.project_id &&
          documentPrefill.project_id
        ) {
          normalizedPayload.project_id = documentPrefill.project_id;
        }
        if (
          !normalizedPayload.organization_id &&
          documentPrefill.organization_name
        ) {
          const targetOrgName = normalizeName(documentPrefill.organization_name);
          const match = targetOrgName
            ? organizations.find(
                (org) => normalizeName(org.name) === targetOrgName
              )
            : undefined;
          if (match) {
            normalizedPayload.organization_id = match.id;
          }
        }
        if (
          !normalizedPayload.project_id &&
          documentPrefill.project_name
        ) {
          const targetProjectName = normalizeName(documentPrefill.project_name);
          const match = targetProjectName
            ? projects.find((project) => {
                const sameOrg = normalizedPayload.organization_id
                  ? project.organizationId ===
                    normalizedPayload.organization_id
                  : true;
                return (
                  sameOrg && normalizeName(project.name) === targetProjectName
                );
              })
            : undefined;
          if (match) {
            normalizedPayload.project_id = match.id;
          }
        }
      }

      if (initiatingDocId && !prefillReserved) {
        try {
          await enhancedApi.requestDraftForDocument(initiatingDocId);
        } catch (error) {
          console.error(
            '[LetterWorkflowPage] Unable to reserve draft for document',
            error
          );
          throw error;
        }
      }

      const created = await handleLetterInitiation(normalizedPayload);
      await fetchLetters();
      setSelectedLetter(created);
      setSelectedLetterId(created.id);
      clearInitiationContext();
    },
    [
      handleLetterInitiation,
      fetchLetters,
      setSelectedLetter,
      initiatingDocId,
      documentPrefill,
      clearInitiationContext,
      organizations,
      projects,
      prefillReserved,
    ]
  );

  const handleRequestInput = useCallback(
    async (
      letterId: string,
      requestDetails: string,
      requestedUserId: string,
      dueDate?: Date
    ) => {
      await handleInputRequest(
        letterId,
        {
          requested_from: requestedUserId,
          details: requestDetails,
          due_date: dueDate ? dueDate.toISOString() : undefined,
        }
      );
      await fetchLetters();
    },
    [handleInputRequest, fetchLetters]
  );

  const handleAssignDrafter = useCallback(
    async (
      letterId: string,
      payload: {
        user_id: string;
        drafting_profile:
          | 'contractor'
          | 'engineer_representation'
          | 'employer_contract_review';
      }
    ) => {
      await assignContractDrafter(letterId, payload);
      await fetchLetters();
    },
    [assignContractDrafter, fetchLetters]
  );

  const handleAssignReviewer = useCallback(
    async (
      letterId: string,
      runId: string,
      payload: {
        reviewer_user_id: string;
        due_at?: string;
        note?: string;
      }
    ) => {
      await assignReviewer(letterId, runId, payload);
      await fetchLetters();
    },
    [assignReviewer, fetchLetters]
  );

  const handleSelectLetter = useCallback(
    (letterId: string) => {
      setSelectedLetterId(letterId);
      const found = letters.find((entry) => entry.id === letterId);
      if (found) {
        setSelectedLetter(found);
      }
    },
    [letters, setSelectedLetter]
  );

  return (
    <div className="container mx-auto p-6 space-y-6">
      <LetterWorkflowHeader
        isInitiateDialogOpen={isInitiateDialogOpen}
        setIsInitiateDialogOpen={setIsInitiateDialogOpen}
        isRequestInputDialogOpen={isRequestInputDialogOpen}
        setIsRequestInputDialogOpen={setIsRequestInputDialogOpen}
        onLetterInitiation={handleInitiation}
      onInputRequest={handleRequestInput}
      users={users}
      organizations={organizations}
      projects={projects}
      selectedLetter={selectedUiLetter}
      selectedLetterId={selectedLetterId}
      canAssignDrafter={activeTab === 'All' && canAssignDrafter}
      onAssignDrafter={handleAssignDrafter}
      onAssignReviewer={handleAssignReviewer}
      documentPrefill={documentPrefill ?? undefined}
      onInitiationDialogClose={clearInitiationContext}
    />

      {error && (
        <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-sm text-destructive">
          {error}
        </div>
      )}

      <LetterWorkflowTabs activeTab={activeTab} setActiveTab={setActiveTab}>
        {isLoading ? (
          <Skeleton className="h-64 w-full" />
        ) : (
          <LettersTable
            letters={filteredLetters}
            formatDate={formatDate}
            onSelectLetter={handleSelectLetter}
            selectedLetterId={selectedLetterId}
          />
        )}
      </LetterWorkflowTabs>
    </div>
  );
};

export default LetterWorkflowPage;
