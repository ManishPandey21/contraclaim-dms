import React, { useEffect, useState } from "react";
import { Plus, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import DocumentPicker from "@/components/pickers/DocumentPicker";
import LetterPicker from "@/components/pickers/LetterPicker";
import type { ClaimHeadLinks } from "@/lib/claim-head-links";
import { listMatrixRows } from "@/services/arbitration-cases-api";

interface Props {
  caseId: string;
  projectId: string;
  value: ClaimHeadLinks;
  onChange: (next: ClaimHeadLinks) => void;
  disabled?: boolean;
}

/**
 * The provision relied on, the notice given and the records proving the figure,
 * chosen rather than typed as ids. The Statement of Claim validator reports a
 * head missing any of these, so a head entered by hand can now be complete.
 *
 * Clauses come from the case's own clause matrix; a clause number not yet in
 * it can be added by number. Notices are letters on the project; evidence is
 * any project document. The server re-checks scope on every id.
 */
const ClaimHeadLinksPicker: React.FC<Props> = ({ caseId, projectId, value, onChange, disabled = false }) => {
  const [clauses, setClauses] = useState<string[]>([]);
  const [typed, setTyped] = useState("");

  useEffect(() => {
    if (!caseId) return;
    let active = true;
    listMatrixRows(caseId, "clause-matrix")
      .then((rows) => {
        if (!active) return;
        const numbers = rows
          .map((row) => String((row as unknown as Record<string, unknown>).clause_number ?? "").trim())
          .filter(Boolean);
        setClauses(Array.from(new Set(numbers)).sort((a, b) => a.localeCompare(b, undefined, { numeric: true })));
      })
      .catch(() => {
        if (active) setClauses([]);
      });
    return () => {
      active = false;
    };
  }, [caseId]);

  const toggleClause = (clause: string) => {
    const has = value.clause_ids.includes(clause);
    onChange({ ...value, clause_ids: has ? value.clause_ids.filter((item) => item !== clause) : [...value.clause_ids, clause] });
  };

  const addTyped = () => {
    const clause = typed.trim();
    if (!clause || value.clause_ids.includes(clause)) return;
    onChange({ ...value, clause_ids: [...value.clause_ids, clause] });
    setTyped("");
  };

  const offered = Array.from(new Set([...clauses, ...value.clause_ids]));

  return (
    <div className="space-y-4 rounded-md border p-3">
      <p className="text-xs text-muted-foreground">
        A head of claim is pleaded with the clause relied on, the notice given and the records that prove the figure. A head
        missing any of them is reported by the Statement of Claim check.
      </p>
      <fieldset className="space-y-2" disabled={disabled}>
        <legend className="text-sm font-medium">Clauses relied on</legend>
        {offered.length === 0 ? (
          <p className="text-xs text-muted-foreground">No clause is in this case's clause matrix yet. Add one by number.</p>
        ) : (
          <div className="flex flex-wrap gap-2">
            {offered.map((clause) => {
              const chosen = value.clause_ids.includes(clause);
              return (
                <Button
                  key={clause}
                  type="button"
                  size="sm"
                  variant={chosen ? "default" : "outline"}
                  aria-pressed={chosen}
                  onClick={() => toggleClause(clause)}
                >
                  Clause {clause}
                </Button>
              );
            })}
          </div>
        )}
        <div className="flex gap-2">
          <Input
            aria-label="Add a clause by number"
            placeholder="Clause number, e.g. 8.4"
            value={typed}
            onChange={(event) => setTyped(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                addTyped();
              }
            }}
          />
          <Button type="button" variant="outline" aria-label="Add clause" onClick={addTyped}>
            <Plus className="h-4 w-4" />
          </Button>
        </div>
        {value.clause_ids.length > 0 && (
          <div className="flex flex-wrap gap-1" aria-label="Chosen clauses">
            {value.clause_ids.map((clause) => (
              <Badge key={clause} variant="neutral" className="gap-1">
                Clause {clause}
                <button type="button" aria-label={`Remove clause ${clause}`} onClick={() => toggleClause(clause)}>
                  <X className="h-3 w-3" />
                </button>
              </Badge>
            ))}
          </div>
        )}
      </fieldset>
      <LetterPicker
        projectId={projectId}
        value={value.notice_ids}
        onChange={(ids) => onChange({ ...value, notice_ids: ids })}
        label="Notice given"
        disabled={disabled}
      />
      <DocumentPicker
        projectId={projectId}
        value={value.evidence_ids}
        onChange={(ids) => onChange({ ...value, evidence_ids: ids })}
        label="Records proving the amount or days"
        disabled={disabled}
      />
    </div>
  );
};

export default ClaimHeadLinksPicker;
