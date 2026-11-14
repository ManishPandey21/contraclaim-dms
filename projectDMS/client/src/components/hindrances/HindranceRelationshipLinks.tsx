import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Link2, Loader2, Trash2 } from "lucide-react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { HINDRANCE_LINK_TARGET_LABELS, formatRegisterDate } from "@/lib/hindrance-labels";
import { getKeyDateWorkflow, getMilestones } from "@/services/key-dates-api";
import {
  linkHindrance,
  listProgrammeMilestones,
  unlinkHindrance,
  type HindranceLinkDTO,
  type HindranceLinkTargetType,
} from "@/services/hindrance-api";

type Candidate = { id: string; label: string; detail?: string };

async function loadCandidates(targetType: HindranceLinkTargetType, projectId: string): Promise<Candidate[]> {
  if (targetType === "programme_milestone") {
    const rows = await listProgrammeMilestones(projectId);
    return rows.map((row) => ({
      id: row.id,
      label: row.milestone_ref || row.title,
      detail: [row.title, row.milestone_type.replace(/_/g, " "), formatRegisterDate(row.planned_date)].filter(Boolean).join(" · "),
    }));
  }
  if (targetType === "key_date") {
    const rows = await getMilestones({ project_id: projectId });
    return rows.map((row) => ({
      id: row.id,
      label: row.milestone_ref || row.title,
      detail: [row.title, formatRegisterDate(row.current_approved_key_date || row.calculated_key_date)].filter(Boolean).join(" · "),
    }));
  }
  const workflow = await getKeyDateWorkflow(projectId);
  return (workflow.submissions || []).map((row) => ({
    id: row.id,
    label: row.revision_label,
    detail: [row.status, formatRegisterDate(row.contractor_submission_date)].filter(Boolean).join(" · "),
  }));
}

function errorDetail(error: unknown, fallback: string): string {
  const detail = (error as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
  return typeof detail === "string" ? detail : fallback;
}

export default function HindranceRelationshipLinks({
  hindranceId,
  projectId,
  targetType,
  links,
  canManage,
  archived,
  onChanged,
}: {
  hindranceId: string;
  projectId: string;
  targetType: HindranceLinkTargetType;
  links: HindranceLinkDTO[];
  canManage: boolean;
  archived: boolean;
  onChanged: () => void;
}) {
  const noun = HINDRANCE_LINK_TARGET_LABELS[targetType];
  // Sentence case inside running text, but an acronym stays an acronym.
  const lowerNoun = noun.replace(/^[A-Z](?=[a-z])/, (first) => first.toLowerCase());
  const [picking, setPicking] = useState(false);
  const [candidates, setCandidates] = useState<Candidate[] | null>(null);
  const [candidateError, setCandidateError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const linkedIds = useMemo(() => new Set(links.map((link) => link.target_id)), [links]);

  const open = async () => {
    setPicking(true);
    setCandidateError(null);
    setCandidates(null);
    try {
      setCandidates(await loadCandidates(targetType, projectId));
    } catch (error) {
      const status = (error as { response?: { status?: number } })?.response?.status;
      setCandidateError(status === 403 ? `You do not have access to ${lowerNoun} records.` : `${noun} records could not be loaded.`);
    }
  };

  const visible = useMemo(() => {
    const text = query.trim().toLowerCase();
    return (candidates || []).filter(
      (candidate) => !text || `${candidate.label} ${candidate.detail || ""}`.toLowerCase().includes(text),
    );
  }, [candidates, query]);

  const link = async (candidate: Candidate) => {
    setBusy(true);
    try {
      await linkHindrance(hindranceId, { target_type: targetType, target_id: candidate.id });
      toast.success(`${candidate.label} linked`);
      setPicking(false);
      setQuery("");
      onChanged();
    } catch (error) {
      toast.error(errorDetail(error, `Failed to link ${lowerNoun}`));
    } finally {
      setBusy(false);
    }
  };

  const unlink = async (item: HindranceLinkDTO) => {
    setBusy(true);
    try {
      await unlinkHindrance(hindranceId, item.id, `${noun} unlinked from register entry`);
      toast.success("Link removed");
      onChanged();
    } catch (error) {
      toast.error(errorDetail(error, "Failed to remove link; reload before retrying"));
    } finally {
      setBusy(false);
    }
  };

  const editable = canManage && !archived;

  return (
    <div className="space-y-2">
      {links.length === 0 ? (
        <p className="text-sm text-muted-foreground">No {lowerNoun} links.</p>
      ) : (
        links.map((item) => {
          const label = item.target?.label || item.target_id;
          const body = (
            <span className="min-w-0 flex-1">
              <span className="block truncate font-medium">{label}</span>
              <span className="block truncate text-xs text-muted-foreground">
                {!item.target_available
                  ? "The linked record no longer exists"
                  : item.target_restricted
                    ? "You do not have access to this record"
                    : [item.target?.title, item.target?.status, formatRegisterDate(item.target?.date)].filter((part) => part && part !== "—").join(" · ")}
              </span>
            </span>
          );
          return (
            <div key={item.id} className="flex items-center gap-2 rounded-md border p-2 text-sm">
              <Link2 className="h-4 w-4 shrink-0" aria-hidden="true" />
              {item.target?.route ? (
                <Link to={item.target.route} className="flex min-w-0 flex-1 hover:underline">{body}</Link>
              ) : body}
              {editable && (
                <Button type="button" variant="ghost" size="sm" aria-label={`Unlink ${label}`} disabled={busy}
                  onClick={() => void unlink(item)}>
                  <Trash2 className="h-4 w-4" />
                </Button>
              )}
            </div>
          );
        })
      )}

      {editable && !picking && (
        <Button type="button" variant="outline" size="sm" onClick={() => void open()}>
          Link {lowerNoun}
        </Button>
      )}
      {editable && picking && (
        <div className="space-y-2 rounded-md border p-2">
          <div className="flex gap-2">
            <label className="sr-only" htmlFor={`${targetType}-link-search`}>Search {lowerNoun} records</label>
            <input id={`${targetType}-link-search`} value={query} placeholder={`Filter ${lowerNoun} records`}
              className="h-9 flex-1 rounded-md border border-input bg-background px-3 text-sm"
              onChange={(event) => setQuery(event.target.value)} />
            <Button type="button" variant="ghost" size="sm" onClick={() => setPicking(false)}>Cancel</Button>
          </div>
          {candidateError ? (
            <p className="text-sm text-destructive">{candidateError}</p>
          ) : candidates === null ? (
            <p className="flex items-center text-sm text-muted-foreground"><Loader2 className="mr-2 h-4 w-4 animate-spin" /> Loading…</p>
          ) : visible.length === 0 ? (
            <p className="text-sm text-muted-foreground">No {lowerNoun} records in this project.</p>
          ) : (
            <ul className="max-h-60 space-y-1 overflow-y-auto">
              {visible.map((candidate) => (
                <li key={candidate.id} className="flex items-center justify-between gap-2 rounded-md border p-2 text-sm">
                  <span className="min-w-0">
                    <span className="block truncate font-medium">{candidate.label}</span>
                    {candidate.detail && <span className="block truncate text-xs text-muted-foreground">{candidate.detail}</span>}
                  </span>
                  {linkedIds.has(candidate.id) ? (
                    <span className="shrink-0 text-xs text-muted-foreground">Already linked</span>
                  ) : (
                    <Button type="button" variant="outline" size="sm" disabled={busy} aria-label={`Link ${candidate.label}`}
                      onClick={() => void link(candidate)}>Link</Button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
