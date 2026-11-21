import React, { useEffect, useState } from "react";

import { Label } from "@/components/ui/label";
import { humanise } from "@/lib/plain-language";
import { type ArbitrationDraftVersion, listArbitrationDraftVersions } from "@/services/arbitration-drafting-api";

interface Props {
  /** The pleading whose versions are offered; nothing is offered until one is chosen. */
  draftId: string;
  value: string;
  onChange: (versionId: string) => void;
  label: string;
  disabled?: boolean;
}

const select = "h-9 w-full rounded-md border border-input bg-background px-2 text-sm disabled:opacity-50";

/**
 * Choose an immutable version of a pleading by its number and validation state (plan
 * 4B-40), instead of typing its id. The server re-checks the version belongs to the draft.
 */
const DraftVersionPicker: React.FC<Props> = ({ draftId, value, onChange, label, disabled = false }) => {
  const [versions, setVersions] = useState<ArbitrationDraftVersion[]>([]);
  const [failed, setFailed] = useState(false);
  const id = `version-picker-${label.replace(/\W+/g, "-").toLowerCase()}`;

  useEffect(() => {
    setVersions([]);
    setFailed(false);
    if (!draftId) return;
    let active = true;
    listArbitrationDraftVersions(draftId)
      .then((rows) => active && setVersions([...rows].sort((a, b) => b.version - a.version)))
      .catch(() => active && setFailed(true));
    return () => {
      active = false;
    };
  }, [draftId]);

  return (
    <div className="space-y-1">
      <Label htmlFor={id}>{label}</Label>
      <select id={id} className={select} value={value} disabled={disabled || !draftId} onChange={(event) => onChange(event.target.value)}>
        <option value="">{draftId ? "Choose a version" : "Choose the pleading first"}</option>
        {versions.map((version) => (
          <option key={version._id} value={version._id}>
            {`Version ${version.version}${version.validation_status ? ` · ${humanise(String(version.validation_status))}` : ""}`}
          </option>
        ))}
      </select>
      {failed && <p role="alert" className="text-xs text-destructive">The versions could not be loaded.</p>}
    </div>
  );
};

export default DraftVersionPicker;
