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
    handleInputRequest,
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
          if (prefill.organization_id && !window.localStorage.getItem('org_id')) {
            window.localStorage.setItem(
              'org_id',
              String(prefill.organization_id)
            );
          }
          if (prefill.project_id && !window.localStorage.getItem('proj_id')) {
            window.localStorage.setItem(
              'proj_id',
              String(prefill.project_id)
            );
          }
        } catch {
          // Storage failures are non-blocking
        }
      }
    };

    loadPrefill();

    return () => {
      isActive = false;
    };
  }, [initiatingDocId]);

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

  const filteredLetters = useMemo(() => {
    if (activeTab === 'All') return uiLetters;
    return uiLetters.filter((letter) => letter.status === activeTab);
  }, [uiLetters, activeTab]);

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
      selectedLetterId={selectedLetterId}
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
