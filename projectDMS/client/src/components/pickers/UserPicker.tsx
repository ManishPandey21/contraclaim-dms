import React, { useEffect, useState } from "react";
import { Loader2, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { errorMessage } from "@/lib/plain-language";
import { listPickerUsers, type PickerUser } from "@/services/pickers-api";

interface Props {
  /** The project the person acts on. The list is empty until one is chosen. */
  projectId: string;
  /** The authority the action needs, e.g. "dms.claim.determine". Only people holding it are offered. */
  permission: string;
  value: string;
  onChange: (userId: string, person: PickerUser | null) => void;
  label: string;
  /** Explains, in plain words, who appears in the list. */
  hint?: string;
  disabled?: boolean;
  /** People who may not be chosen here (for example, the author of the work under review). */
  excludeIds?: string[];
}

/**
 * Choose a person for a role on one project (plan 4B-11, C-21).
 *
 * The server returns only active project members holding `permission`, so
 * everyone offered is someone the action will accept. Only names are shown.
 */
const UserPicker: React.FC<Props> = ({ projectId, permission, value, onChange, label, hint, disabled = false, excludeIds = [] }) => {
  const [q, setQ] = useState("");
  const [people, setPeople] = useState<PickerUser[]>([]);
  const [truncated, setTruncated] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const inputId = `user-picker-${permission.replace(/\W+/g, "-")}`;

  useEffect(() => {
    if (!projectId) {
      setPeople([]);
      return;
    }
    let active = true;
    setLoading(true);
    setError(null);
    listPickerUsers({ projectId, permission, q: search })
      .then((response) => {
        if (!active) return;
        setPeople(response.items);
        setTruncated(response.truncated);
      })
      .catch((err) => {
        if (!active) return;
        setPeople([]);
        setError(errorMessage(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [projectId, permission, search]);

  const offered = people.filter((person) => !excludeIds.includes(person.id));

  return (
    <div className="space-y-2">
      <Label htmlFor={inputId}>{label}</Label>
      {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      {(truncated || search) && (
        <div className="flex gap-2">
          <Input
            aria-label={`Search ${label.toLowerCase()}`}
            placeholder="Search by name"
            value={q}
            disabled={disabled}
            onChange={(event) => setQ(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter") {
                event.preventDefault();
                setSearch(q.trim());
              }
            }}
          />
          <Button type="button" variant="outline" aria-label="Search" disabled={disabled} onClick={() => setSearch(q.trim())}>
            <Search className="h-4 w-4" />
          </Button>
        </div>
      )}
      <select
        id={inputId}
        className="w-full rounded-md border bg-background px-3 py-2 text-sm"
        value={value}
        disabled={disabled || !projectId || loading}
        onChange={(event) => {
          const person = offered.find((item) => item.id === event.target.value) ?? null;
          onChange(event.target.value, person);
        }}
      >
        <option value="">{projectId ? "Choose a person" : "Choose a project first"}</option>
        {offered.map((person) => (
          <option key={person.id} value={person.id}>
            {person.name}
          </option>
        ))}
      </select>
      {loading && (
        <p className="flex items-center gap-1 text-xs text-muted-foreground">
          <Loader2 className="h-3 w-3 animate-spin" /> Loading people
        </p>
      )}
      {!loading && projectId && !error && offered.length === 0 && (
        <p className="text-xs text-muted-foreground">No one on this project holds this authority yet. Ask your administrator to grant it.</p>
      )}
      {truncated && <p className="text-xs text-muted-foreground">More people match. Search by name to narrow the list.</p>}
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
    </div>
  );
};

export default UserPicker;
