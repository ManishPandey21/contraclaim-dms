import React, { useEffect, useMemo, useState, useCallback } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { api } from "@/services/api";
import LetterWorkflowPanel from "@/components/letter-workflow/LetterWorkflowPanel";
import type {
  Letter as UiLetter,
  User as UiUser,
} from "@/components/letter-workflow/types";
import { Button } from "@/components/ui/button";

// Minimal placeholder user resolver (since backend returns ids only)
const makeUser = (id?: string): UiUser => ({
  id: id || "",
  name: "User",
  email: "",
});

const normalizeBackendLetterToUi = (l: any): UiLetter => {
  const id = String(l?.id ?? l?._id ?? "");
  const createdById = String(
    l?.created_by ?? l?.createdBy ?? l?.created_by_id ?? ""
  );
  const assignedToId = String(l?.assigned_to ?? l?.assignedTo?.id ?? "");
  return {
    id,
    title: String(l?.title ?? ""),
    recipient: String(l?.recipient ?? ""),
    subject: String(l?.subject ?? ""),
    content: String(l?.content ?? ""),
    status: l?.status ?? "Draft",
    createdBy: makeUser(createdById),
    assignedTo: makeUser(assignedToId),
    organizationId: l?.organization_id ?? l?.organizationId ?? undefined,
    projectId: l?.project_id ?? l?.projectId ?? undefined,
    createdAt: l?.created_at ?? l?.createdAt ?? new Date().toISOString(),
    updatedAt: l?.updated_at ?? l?.updatedAt ?? new Date().toISOString(),
    statusStartDate: l?.statusStartDate ?? undefined,
    comments: Array.isArray(l?.comments) ? l.comments : [],
    inputRequests: Array.isArray(l?.inputRequests) ? l.inputRequests : [],
    reference: l?.reference
      ? {
          id: String(l.reference.id ?? ""),
          title: String(l.reference.title ?? ""),
          subject: String(l.reference.subject ?? ""),
          date: String(l.reference.date ?? ""),
          referenceNumber: String(
            l.reference.reference_number ?? l.reference.referenceNumber ?? ""
          ),
        }
      : undefined,
  };
};

const buildPatchFromUi = (u: UiLetter): any => {
  const patch: any = {
    title: u.title,
    recipient: u.recipient,
    subject: u.subject,
    content: u.content,
    status: u.status,
    comments: u.comments,
  };
  if (u.reference) {
    patch.reference = {
      id: u.reference.id,
      title: u.reference.title,
      subject: u.reference.subject,
      date: u.reference.date,
      reference_number: u.reference.referenceNumber,
    };
  }
  return patch;
};

const LetterDetailPage: React.FC = () => {
  const params = useParams();
  const navigate = useNavigate();
  const letterId = useMemo(
    () => String(params.id ?? params.letterId ?? ""),
    [params]
  );
  const [letter, setLetter] = useState<UiLetter | null>(null);
  const [loading, setLoading] = useState<boolean>(false);
  const [err, setErr] = useState<string | null>(null);

  const loadLetter = useCallback(async () => {
    if (!letterId) return;
    try {
      setLoading(true);
      setErr(null);
      const { data } = await api.get(`/letters/${letterId}`);
      setLetter(normalizeBackendLetterToUi(data));
    } catch (e: any) {
      setErr(e?.response?.data?.detail || "Failed to load letter");
    } finally {
      setLoading(false);
    }
  }, [letterId]);

  useEffect(() => {
    loadLetter();
  }, [loadLetter]);

  const onLetterUpdate = useCallback(
    async (updatedUiLetter: UiLetter) => {
      try {
        const patch = buildPatchFromUi(updatedUiLetter);
        const { data } = await api.put(`/letters/${letterId}`, patch);
        setLetter(normalizeBackendLetterToUi(data));
      } catch (e: any) {
        setErr(e?.response?.data?.detail || "Failed to update letter");
        throw e;
      }
    },
    [letterId]
  );

  const onCancel = () => {
    // Navigate back to letters list
    navigate("/letters");
  };

  return (
    <div className="w-[98vw] max-w-[1200px] mx-auto px-6 py-4">
      <div className="flex items-center justify-between mb-4">
        <h1 className="text-2xl font-semibold">Letter</h1>
        <div className="flex items-center gap-2">
          <Button variant="outline" onClick={() => navigate("/letters")}>
            Back to Letters
          </Button>
        </div>
      </div>

      {loading && <div>Loading letter...</div>}
      {err && <div className="text-sm text-red-600 mb-3">{err}</div>}

      {letter && (
        <LetterWorkflowPanel
          selectedLetter={letter}
          onLetterUpdate={onLetterUpdate}
          onCancel={onCancel}
        />
      )}
    </div>
  );
};

export default LetterDetailPage;
